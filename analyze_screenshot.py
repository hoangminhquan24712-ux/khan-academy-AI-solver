"""
Screenshot Analyzer — Công cụ phụ trợ
======================================
Dùng AI phân tích ảnh chụp màn hình bài tập Khan Academy
để debug hoặc kiểm tra trước khi chạy auto-solver.
"""

import base64
import sys
from pathlib import Path
from openai import OpenAI

AI_API_KEY  = "sk-xt-aee3b14cf693ac76aeb0259997e6006551c2e652e9511d13"
AI_BASE_URL = "https://api.xkiro.com/v1"
AI_MODEL    = "qwen/qwen3.8-max:free"

client = OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)


def analyze_screenshot(image_path: str) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    response = client.chat.completions.create(
        model=AI_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                },
                {
                    "type": "text",
                    "text": (
                        "Đây là ảnh chụp màn hình bài tập Khan Academy. "
                        "Hãy phân tích và cho biết:\n"
                        "1. Loại câu hỏi (trắc nghiệm / điền đáp án / toán học / khác)\n"
                        "2. Nội dung câu hỏi\n"
                        "3. Đáp án đúng\n"
                        "4. Lý do"
                    ),
                },
            ],
        }],
    )
    return response.choices[0].message.content.strip()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Dùng: python analyze_screenshot.py <path_to_screenshot.png>")
        sys.exit(1)

    img = sys.argv[1]
    if not Path(img).exists():
        print(f"Không tìm thấy file: {img}")
        sys.exit(1)

    print(f"📸 Đang phân tích: {img}")
    result = analyze_screenshot(img)
    print("\n🤖 Kết quả phân tích:")
    print("─" * 50)
    print(result)
