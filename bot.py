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
from openpyxl.utils import get_column_letter

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not set")

app = Flask(__name__)

user_settings = {}
DEFAULT_CELLS = 50

def process_image_to_matrix(image_bytes, target_cells):
    """
    Улучшенная стабильная версия:
    - повышение контраста (CLAHE)
    - лёгкое размытие для удаления шумов
    - адаптивная бинаризация с чувствительными параметрами
    - разбиение на ячейки с усреднением
    """
    # Загружаем изображение
    image = Image.open(io.BytesIO(image_bytes))
    img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    # 1. Повышение контраста (CLAHE)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8,8))
    gray = clahe.apply(gray)

    # 2. Лёгкое размытие для удаления высокочастотного шума
    gray = cv2.medianBlur(gray, 3)

    # 3. Адаптивная бинаризация (более чувствительная, чем раньше)
    binary = cv2.adaptiveThreshold(gray, 255,
                                   cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 15, 3)  # параметры 15 и 3

    # 4. Морфология для замыкания мелких дырок
    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    h, w = cleaned.shape
    target_cols = target_cells
    target_rows = max(1, int(target_cols * (h / w)))
    if target_rows > 200:
        target_rows = 200
        target_cols = int(target_rows * (w / h))

    matrix = []
    scheme = np.zeros((target_rows, target_cols), dtype=np.uint8)

    for row in range(target_rows):
        row_str = []
        y_start = int(row * h / target_rows)
        y_end = int((row + 1) * h / target_rows)
        for col in range(target_cols):
            x_start = int(col * w / target_cols)
            x_end = int((col + 1) * w / target_cols)
            block = cleaned[y_start:y_end, x_start:x_end]
            if block.size == 0:
                filled_ratio = 0
            else:
                filled_ratio = np.sum(block == 255) / block.size
            # Порог 0.4 (был 0.5) - делает схему чуть более заполненной, сохраняя детали
            if filled_ratio > 0.4:
                scheme[row, col] = 255
                row_str.append('1')
            else:
                row_str.append('0')
        matrix.append(''.join(row_str))

    scheme_pil = Image.fromarray(scheme, mode='L')
    return scheme_pil, matrix

def generate_excel_bytes(matrix):
    wb = Workbook()
    ws = wb.active
    ws.title = "Scheme"
    black_fill = PatternFill(start_color="000000", end_color="000000", fill_type="solid")
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    # Заголовки столбцов
    for col_idx in range(1, len(matrix[0]) + 1):
        cell = ws.cell(row=1, column=col_idx + 1)
        cell.value = col_idx
        cell.alignment = Alignment(horizontal="center", vertical="center")
    # Номера строк
    for row_idx in range(1, len(matrix) + 1):
        cell = ws.cell(row=row_idx + 1, column=1)
        cell.value = row_idx
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Заполнение
    for i, row_str in enumerate(matrix):
        for j, ch in enumerate(row_str):
            cell = ws.cell(row=i + 2, column=j + 2)
            cell.value = int(ch)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if ch == '1':
                cell.fill = black_fill
                cell.font = Font(color="FFFFFF")
            else:
                cell.fill = white_fill
                cell.font = Font(color="000000")

    # Настройка размера ячеек Excel
    for col in range(2, len(matrix[0]) + 2):
        ws.column_dimensions[get_column_letter(col)].width = 3
    for row in range(2, len(matrix) + 2):
        ws.row_dimensions[row].height = 15

    excel_buffer = io.BytesIO()
    wb.save(excel_buffer)
    excel_buffer.seek(0)
    return excel_buffer

def generate_description_txt(matrix):
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

def send_menu_keyboard(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    keyboard = {
        "keyboard": [
            ["🐭 Маленький", "🐰 Средний"],
            ["🐘 Большой", "📏 Свой размер"],
            ["❓ Помощь"]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }
    payload = {
        'chat_id': chat_id,
        'text': text,
        'reply_markup': keyboard
    }
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

        # преобразование кнопок
        if text == "🐭 Маленький":
            text = "/small"
        elif text == "🐰 Средний":
            text = "/medium"
        elif text == "🐘 Большой":
            text = "/big"
        elif text == "📏 Свой размер":
            text = "/cells"
        elif text == "❓ Помощь":
            text = "/help"

        # команды
        if text.startswith('/big'):
            user_settings[chat_id] = 50
            send_message(chat_id, "✅ Установлен большой размер схемы (~50 ячеек по ширине).")
            return jsonify({'status': 'ok'})

        if text.startswith('/medium'):
            user_settings[chat_id] = 25
            send_message(chat_id, "✅ Установлен средний размер схемы (~25 ячеек по ширине).")
            return jsonify({'status': 'ok'})

        if text.startswith('/small'):
            user_settings[chat_id] = 15
            send_message(chat_id, "✅ Установлен маленький размер схемы (~15 ячеек по ширине).")
            return jsonify({'status': 'ok'})

        if text.startswith('/cells'):
            parts = text.split()
            if len(parts) == 2:
                try:
                    val = int(parts[1])
                    if 5 <= val <= 200:
                        user_settings[chat_id] = val
                        send_message(chat_id, f"✅ Количество ячеек по ширине установлено: {val}.")
                    else:
                        send_message(chat_id, "❌ Введите число от 5 до 200.")
                except:
                    send_message(chat_id, "❌ Используйте: /cells <число>")
            else:
                send_message(chat_id, "Введите количество ячеек по ширине числом от 5 до 200. Например: /cells 30")
            return jsonify({'status': 'ok'})

        if text.startswith('/start') or text.startswith('/help'):
            help_text = "🧶 Выберите размер будущего изделия:"
            send_menu_keyboard(chat_id, help_text)
            return jsonify({'status': 'ok'})

        # обработка фото
        if 'photo' not in message:
            return jsonify({'status': 'ok'})

        target_cells = user_settings.get(chat_id, DEFAULT_CELLS)

        # скачиваем фото
        photo_obj = message['photo'][-1]
        file_id = photo_obj['file_id']
        file_info = requests.get(f"https://api.telegram.org/bot{TOKEN}/getFile?file_id={file_id}").json()
        if not file_info['ok']:
            raise Exception("Не удалось получить информацию о файле")

        file_path = file_info['result']['file_path']
        photo_bytes = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}").content

        # генерируем схему
        scheme_image, matrix = process_image_to_matrix(photo_bytes, target_cells)

        # PNG
        png_buffer = io.BytesIO()
        scheme_image.save(png_buffer, format='PNG')
        png_buffer.seek(0)
        send_photo(chat_id, png_buffer, f"📐 Схема (ширина {len(matrix[0])} ячеек, высота {len(matrix)})")

        # Excel
        excel_buffer = generate_excel_bytes(matrix)
        send_document(chat_id, excel_buffer, "scheme.xlsx", "📊 Excel-схема: 0=белый, 1=чёрный")

        # текстовое описание
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
