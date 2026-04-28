import logging
from flask import Flask, request, jsonify
import cv2
import numpy as np
from PIL import Image
import io
import os
import requests
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Alignment, Font

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not set")

app = Flask(__name__)

# Хранилище настроек пользователей: {chat_id: cell_size}
user_settings = {}

def process_image_to_matrix(image_bytes, cell_size):
    """Возвращает (PIL Image схемы, матрица 0/1 в виде списка строк)"""
    image = Image.open(io.BytesIO(image_bytes))
    img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    binary = cv2.adaptiveThreshold(gray, 255,
                                   cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 11, 2)

    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    height, width = cleaned.shape
    rows = height // cell_size
    cols = width // cell_size
    if rows == 0 or cols == 0:
        raise ValueError("Изображение слишком маленькое для выбранного размера ячейки")

    matrix = []  # список строк из '0'/'1'
    scheme = np.zeros((rows, cols), dtype=np.uint8)

    for y in range(rows):
        row_str_list = []
        for x in range(cols):
            start_y = y * cell_size
            start_x = x * cell_size
            cell = cleaned[start_y:start_y + cell_size, start_x:start_x + cell_size]
            filled_ratio = np.sum(cell == 255) / (cell_size * cell_size)
            if filled_ratio > 0.5:
                scheme[y, x] = 255
                row_str_list.append('1')
            else:
                row_str_list.append('0')
        matrix.append(''.join(row_str_list))

    scheme_pil = Image.fromarray(scheme, mode='L')
    return scheme_pil, matrix

def generate_excel_bytes(matrix):
    """Создаёт Excel-файл с визуальной схемой (чёрные клетки для 1) и возвращает BytesIO"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Scheme"

    # Заливки
    black_fill = PatternFill(start_color="000000", end_color="000000", fill_type="solid")
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    # Заголовки столбцов (номера столбцов) – в первой строке, начиная с B1
    for col_idx in range(1, len(matrix[0]) + 1):
        cell = ws.cell(row=1, column=col_idx + 1)  # +1 из-за первого столбца с номерами строк
        cell.value = col_idx
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Номера строк (в первом столбце, начиная со второй строки)
    for row_idx in range(1, len(matrix) + 1):
        cell = ws.cell(row=row_idx + 1, column=1)
        cell.value = row_idx
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Заполнение схемы
    for i, row_str in enumerate(matrix):
        for j, ch in enumerate(row_str):
            cell = ws.cell(row=i + 2, column=j + 2)  # +2 из-за заголовков
            cell.value = int(ch)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if ch == '1':
                cell.fill = black_fill
                cell.font = Font(color="FFFFFF")  # белый текст на чёрном
            else:
                cell.fill = white_fill
                cell.font = Font(color="000000")  # чёрный текст на белом

    # Настройка ширины столбцов и высоты строк для квадратных ячеек
    from openpyxl.utils import get_column_letter
    for col in range(2, len(matrix[0]) + 2):
        ws.column_dimensions[get_column_letter(col)].width = 3
    for row in range(2, len(matrix) + 2):
        ws.row_dimensions[row].height = 15

    excel_buffer = io.BytesIO()
    wb.save(excel_buffer)
    excel_buffer.seek(0)
    return excel_buffer

def generate_description_txt(matrix):
    """Генерирует текстовое описание в виде группировок: '12 пустых, 7 заполненных, 9 пустых'"""
    lines = []
    for idx, row_str in enumerate(matrix, start=1):
        if not row_str:
            lines.append(f"Ряд {idx}: пустая строка")
            continue
        groups = []
        current = row_str[0]
        count = 1
        for ch in row_str[1:]:
            if ch == current:
                count += 1
            else:
                groups.append(f"{count} {'пустых' if current == '0' else 'заполненных'}")
                current = ch
                count = 1
        groups.append(f"{count} {'пустых' if current == '0' else 'заполненных'}")
        lines.append(f"Ряд {idx}: " + ", ".join(groups))
    return "\n".join(lines)

def send_document(chat_id, file_bytes, filename, caption=""):
    url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
    files = {'document': (filename, file_bytes, 'application/octet-stream')}
    data = {'chat_id': chat_id, 'caption': caption}
    resp = requests.post(url, files=files, data=data)
    return resp.ok

def send_photo(chat_id, photo_bytes, caption=""):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    files = {'photo': photo_bytes}
    data = {'chat_id': chat_id, 'caption': caption}
    resp = requests.post(url, files=files, data=data)
    return resp.ok

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text}
    requests.post(url, json=payload)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
        if not update or 'message' not in update:
            return jsonify({'status': 'ok'})

        message = update['message']
        chat_id = message['chat']['id']
        text = message.get('text', '')

        # Обработка команд
        if text.startswith('/size'):
            parts = text.split()
            if len(parts) == 2:
                try:
                    new_size = int(parts[1])
                    if 5 <= new_size <= 50:
                        user_settings[chat_id] = new_size
                        send_message(chat_id, f"✅ Размер ячейки установлен на {new_size} пикселей.")
                    else:
                        send_message(chat_id, "❌ Размер ячейки должен быть от 5 до 50.")
                except ValueError:
                    send_message(chat_id, "❌ Используйте: /size <число от 5 до 50>")
            else:
                send_message(chat_id, "❌ Пример: /size 20")
            return jsonify({'status': 'ok'})

        if text.startswith('/start') or text.startswith('/help'):
            help_text = (
                "🧶 Бот для филейного вязания\n\n"
                "📸 Отправьте фото, и я превращу его в схему.\n"
                "⚙️ Команды:\n"
                "/size N — установить размер ячейки (5-50), по умолчанию 15\n"
                "/help — эта справка\n\n"
                "Результат: PNG схема, Excel файл (визуальная схема с чёрными клетками для 1), текстовое описание рядами."
            )
            send_message(chat_id, help_text)
            return jsonify({'status': 'ok'})

        # Если нет фото, игнорируем
        if 'photo' not in message:
            return jsonify({'status': 'ok'})

        # Получаем размер ячейки для данного чата (по умолчанию 15)
        cell_size = user_settings.get(chat_id, 15)

        # Скачиваем фото
        photo_obj = message['photo'][-1]
        file_id = photo_obj['file_id']
        file_info = requests.get(f"https://api.telegram.org/bot{TOKEN}/getFile?file_id={file_id}").json()
        if not file_info['ok']:
            raise Exception("Не удалось получить информацию о файле")

        file_path = file_info['result']['file_path']
        photo_bytes = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}").content

        # Обработка
        scheme_image, matrix = process_image_to_matrix(photo_bytes, cell_size)

        # PNG схема
        png_buffer = io.BytesIO()
        scheme_image.save(png_buffer, format='PNG')
        png_buffer.seek(0)
        send_photo(chat_id, png_buffer, f"📐 Схема (ячейка {cell_size} px)")

        # Excel файл (визуальная схема с номерами строк/столбцов)
        excel_buffer = generate_excel_bytes(matrix)
        send_document(chat_id, excel_buffer, "scheme.xlsx", "📊 Excel-схема: 0=белый, 1=чёрный")

        # Текстовое описание (группировки)
        description = generate_description_txt(matrix)
        txt_buffer = io.BytesIO(description.encode('utf-8'))
        txt_buffer.seek(0)
        send_document(chat_id, txt_buffer, "description.txt", "📄 Текстовое описание рядов")

        return jsonify({'status': 'ok'})
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/')
def index():
    return "Бот для филейного вязания работает. Используйте Telegram."

if __name__ == '__main__':
    app.run()
