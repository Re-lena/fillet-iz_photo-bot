import logging
from flask import Flask, request, jsonify
from telegram import Update, Bot
import cv2
import numpy as np
from PIL import Image
import io
import os

logging.basicConfig(level=logging.INFO)

# Берём токен из переменной окружения (безопасно)
TOKEN = os.environ.get('8444213096:AAHsaf5Rr7lUOVguOmh4Oi8MyJ9rw31qIFU')
if not TOKEN:
    raise ValueError("No 8444213096:AAHsaf5Rr7lUOVguOmh4Oi8MyJ9rw31qIFU set")

bot = Bot(token=TOKEN)
app = Flask(__name__)

def process_image_to_knitting_scheme(image_bytes):
    # Загружаем изображение
    image = Image.open(io.BytesIO(image_bytes))
    img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    # Адаптивная бинаризация
    binary = cv2.adaptiveThreshold(gray, 255,
                                   cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 11, 2)

    # Удаление шума
    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    # Размер ячейки (можно позже сделать настраиваемым)
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

    scheme_pil = Image.fromarray(scheme, mode='L')
    return scheme_pil

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        if update.message and update.message.photo:
            # Берём фото в максимальном качестве
            photo_file = update.message.photo[-1].get_file()
            file_bytes = photo_file.download_as_bytearray()
            scheme_image = process_image_to_knitting_scheme(file_bytes)
            output_buffer = io.BytesIO()
            scheme_image.save(output_buffer, format='PNG')
            output_buffer.seek(0)
            update.message.reply_photo(
                photo=output_buffer,
                caption="✅ Ваша схема готова! 1 клетка = 3 столбика с накидом."
            )
        return jsonify({'status': 'ok'})
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/')
def index():
    return "Бот работает. Используйте Telegram."

if __name__ == '__main__':
    app.run()
