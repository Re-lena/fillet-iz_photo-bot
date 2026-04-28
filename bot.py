import logging
from flask import Flask, request, jsonify
import cv2
import numpy as np
from PIL import Image
import io
import os
import requests
import json

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("Переменная окружения TELEGRAM_BOT_TOKEN не установлена!")

app = Flask(__name__)

def process_image_to_knitting_scheme(image_bytes):
    image = Image.open(io.BytesIO(image_bytes))
    img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    binary = cv2.adaptiveThreshold(gray, 255,
                                   cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 11, 2)

    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    cell_size = 15
    height, width = cleaned.shape
    rows = height // cell_size
    cols = width // cell_size

    scheme = np.zeros((rows, cols), dtype=np.uint8)

    for y in range(rows):
        for x in range(cols):
            start_y = y * cell_size
            start_x = x * cell_size
            cell = cleaned[start_y:start_y + cell_size, start_x:start_x + cell_size]
            filled_ratio = np.sum(cell == 255) / (cell_size ** 2)
            if filled_ratio > 0.5:
                scheme[y, x] = 255

    return Image.fromarray(scheme, mode='L')

def send_photo(chat_id, photo_bytes, caption=""):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    files = {'photo': photo_bytes}
    data = {'chat_id': chat_id, 'caption': caption}
    response = requests.post(url, files=files, data=data)
    return response.ok

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
        if not update or 'message' not in update:
            return jsonify({'status': 'ok'})

        message = update['message']
        chat_id = message['chat']['id']

        # Проверяем, есть ли фото
        if 'photo' not in message:
            return jsonify({'status': 'ok'})

        # Берём самый большой размер фото (последний элемент)
        photo_obj = message['photo'][-1]
        file_id = photo_obj['file_id']

        # Получаем ссылку на файл
        file_url = f"https://api.telegram.org/bot{TOKEN}/getFile?file_id={file_id}"
        file_resp = requests.get(file_url).json()
        if not file_resp['ok']:
            raise Exception("Не удалось получить информацию о файле")

        file_path = file_resp['result']['file_path']
        file_download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        photo_bytes = requests.get(file_download_url).content

        # Обрабатываем в схему
        scheme_image = process_image_to_knitting_scheme(photo_bytes)
        output_buffer = io.BytesIO()
        scheme_image.save(output_buffer, format='PNG')
        output_buffer.seek(0)

        # Отправляем схему
        send_photo(chat_id, output_buffer, "✅ Ваша схема готова! 1 клетка = 3 столбика с накидом.")

        return jsonify({'status': 'ok'})
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/')
def index():
    return "Бот работает. Используйте Telegram."

if __name__ == '__main__':
    app.run()
