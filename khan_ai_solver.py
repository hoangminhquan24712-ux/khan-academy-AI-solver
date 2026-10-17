"""
Khan Academy AI Auto-Solver
===========================
Tự động làm bài tập trên Khan Academy bằng AI.
Hỗ trợ: trắc nghiệm, điền đáp án, toán học, v.v.
"""

import os
os.environ.setdefault("PYTHONUTF8", "1")  # Fix Unicode trên Windows

import asyncio
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import msvcrt
except ImportError:
    msvcrt = None

from openai import OpenAI
from playwright.async_api import async_playwright, Page, Browser

# ─────────────── ĐỌC CẤU HÌNH TỪ .env ──────────────
def _load_env(path: str = ".env") -> None:
    """Đọc file .env và set vào os.environ."""
    env_file = Path(path)
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

_load_env()

AI_API_KEY    = os.environ.get("AI_API_KEY",    "sk-xt-aee3b14cf693ac76aeb0259997e6006551c2e652e9511d13")
AI_BASE_URL   = os.environ.get("AI_BASE_URL",   "https://api.xkiro.com/v1")
AI_MODEL      = os.environ.get("AI_MODEL",      "qwen/qwen3.8-max:free")
KA_EMAIL      = os.environ.get("KA_EMAIL",      "hoangminhquan0756")
KA_PASSWORD   = os.environ.get("KA_PASSWORD",   "qnl-601-OGX")
KA_TARGET_URL = os.environ.get("KA_TARGET_URL", "https://www.khanacademy.org/")
CLASSROOM_URL = os.environ.get("CLASSROOM_URL", "https://classroom.khanacademy.org/")
HEADLESS      = os.environ.get("HEADLESS",      "false").lower() == "true"
DELAY_BETWEEN = float(os.environ.get("DELAY_BETWEEN", "1.5"))
AI_PAUSE_SECONDS = float(os.environ.get("AI_PAUSE_SECONDS", "5.0"))
MAX_QUESTIONS = int(os.environ.get("MAX_QUESTIONS", "100"))
SCREENSHOT_DIR = Path("screenshots")
# ─────────────────────────────────────────────────────


client = OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)


# ══════════════════════════════════════════════════
#  AI HELPER
# ══════════════════════════════════════════════════

LAST_AI_ANSWER_TIME = 0.0


def ai_ask(prompt: str, image_b64: str | None = None) -> str:
    """Gửi câu hỏi cho AI, có thể kèm ảnh chụp màn hình."""
    global LAST_AI_ANSWER_TIME
    messages_content = []

    if image_b64:
        messages_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
        })

    messages_content.append({"type": "text", "text": prompt})

    response = client.chat.completions.create(
        model=AI_MODEL,
        messages=[{"role": "user", "content": messages_content}],
    )
    content = response.choices[0].message.content.strip()

    # Đánh dấu thời điểm AI vừa trả lời (để khoan chụp ảnh câu mới trong 5s)
    LAST_AI_ANSWER_TIME = time.time()
    return content


def parse_multi_answers(raw: str, expected_count: int = 1) -> list:
    """
    Trích xuất danh sách các đáp án khi bài tập có 1 hoặc nhiều ô input (tìm x, y, hoặc phương trình).
    """
    # 0. Chuẩn hóa dấu trừ và gạch ngang unicode về dấu trừ ASCII chuẩn
    text = raw.replace('−', '-').replace('–', '-').replace('—', '-')

    # 1. Tìm FINAL_ANSWERS: [...] hoặc FINAL_ANSWER: [...]
    m = re.search(r'FINAL_ANSWERS?\s*:\s*\[([^\]]+)\]', text, re.IGNORECASE)
    if m:
        parts = m.group(1).split(',')
        res = []
        for p in parts:
            item = p.strip().strip(' "\'')
            if not item:
                continue
            # Chỉ strip "x = " nếu vế sau thuần túy là 1 số (ví dụ: "x = 7" -> "7")
            # KHÔNG strip nếu là phương trình (ví dụ: "-5x = 8" hoặc "y = 2x + 1")
            m_assign = re.match(r'^[a-zA-Z]\s*=\s*([-]?\d+(?:[.,]\d+)?(?:/[1-9]\d*)?)$', item)
            if m_assign:
                res.append(m_assign.group(1))
            else:
                res.append(item)
        if res:
            return res[:expected_count] if len(res) >= expected_count else res

    # 1b. Tìm FINAL_ANSWERS?: <phương_trình hoặc giá_trị> (không dùng ngoặc vuông)
    m_single = re.search(r'FINAL_ANSWERS?\s*:\s*([^\r\n]+)', text, re.IGNORECASE)
    if m_single:
        line_val = m_single.group(1).strip().strip(' "\'[]')
        if line_val:
            if '=' in line_val:
                m_assign = re.match(r'^[a-zA-Z]\s*=\s*([-]?\d+(?:[.,]\d+)?(?:/[1-9]\d*)?)$', line_val)
                if m_assign and expected_count > 1:
                    return [m_assign.group(1)]
                return [line_val]
            if ',' in line_val and expected_count > 1:
                sub_parts = [sp.strip().strip(' "\'') for sp in line_val.split(',') if sp.strip()]
                return sub_parts[:expected_count]
            return [line_val]

    # 2. Tìm danh sách JSON [a, b]
    m_json = re.search(r'\[([^\]]+)\]', text)
    if m_json:
        try:
            arr = json.loads('[' + m_json.group(1) + ']')
            if isinstance(arr, list) and arr:
                return [str(x).strip() for x in arr][:expected_count]
        except Exception:
            pass

    # 2b. Tìm phương trình biểu thức: -5x = 8, 3x + 2y = 10, v.v.
    eq_matches = re.findall(r'(-?\s*\d*[a-zA-Z](?:\s*[-+*/]\s*\d*[a-zA-Z])*\s*=\s*-?\s*\d+(?:[.,]\d+)?(?:/[1-9]\d*)?)', text)
    if eq_matches:
        cleaned_eqs = [re.sub(r'\s+', ' ', eq).strip() for eq in eq_matches]
        if expected_count == 1:
            return [cleaned_eqs[-1]]
        return cleaned_eqs[:expected_count]

    # 3. Tìm x = ..., y = ... (khi hỏi giá trị biến số)
    var_matches = re.findall(r'[a-zA-Z]\s*=\s*([-]?\d+(?:[.,]\d+)?(?:/[1-9]\d*)?)', text)
    if len(var_matches) >= expected_count and expected_count > 1:
        return var_matches[:expected_count]

    # 4. Tìm tất cả số hoặc phân số
    nums = re.findall(r'[-]?\d+(?:[.,]\d+)?(?:/[1-9]\d*)?', text)
    if len(nums) >= expected_count:
        return nums[:expected_count]
    elif nums:
        return nums

    return [text.strip()]


def parse_order_answers(raw: str, num_items: int) -> list:
    """
    Parse danh sách thứ tự từ câu trả lời của AI cho bài tập kéo thả / sắp xếp (Matcher / Orderer).
    Trả về danh sách 0-indexed chỉ số của thẻ ban đầu cần xếp vào từng vị trí từ trên xuống dưới.
    Ví dụ: FINAL_ORDER: [2, 1, 3] -> [1, 0, 2]
    """
    # 1. Tìm FINAL_ORDER: [...]
    m = re.search(r'FINAL_ORDER\s*:\s*\[([^\]]+)\]', raw, re.IGNORECASE)
    if m:
        parts = m.group(1).split(',')
        order = []
        for p in parts:
            nums = re.findall(r'\d+', p)
            if nums:
                val = int(nums[0]) - 1
                if 0 <= val < num_items and val not in order:
                    order.append(val)
        if len(order) == num_items:
            return order

    # 2. Tìm danh sách số bất kỳ dạng [1, 2, 3]
    m_bracket = re.search(r'\[([0-9,\s]+)\]', raw)
    if m_bracket:
        nums = [int(n) - 1 for n in re.findall(r'\d+', m_bracket.group(1))]
        valid_nums = [n for n in nums if 0 <= n < num_items]
        seen = set()
        unique = []
        for n in valid_nums:
            if n not in seen:
                seen.add(n)
                unique.append(n)
        if len(unique) == num_items:
            return unique

    # 2b. Tìm các mẫu như "Thẻ 2", "Thẻ [2]", "Card 2"
    card_mentions = re.findall(r'(?:thẻ|card|item|mục)\s*\[?(\d+)\]?', raw, re.IGNORECASE)
    if card_mentions:
        nums = [int(n) - 1 for n in card_mentions if 0 <= int(n) - 1 < num_items]
        seen = set()
        unique = []
        for n in nums:
            if n not in seen:
                seen.add(n)
                unique.append(n)
        if len(unique) == num_items:
            return unique

    # 3. Fallback tìm các số 1..num_items xuất hiện trong text
    nums = [int(n) - 1 for n in re.findall(rf'\b[1-{min(num_items, 9)}]\b', raw)]
    seen = set()
    order = []
    for n in nums:
        if 0 <= n < num_items and n not in seen:
            seen.add(n)
            order.append(n)
    if len(order) == num_items:
        return order

    # Mặc định giữ nguyên nếu không parse được
    return list(range(num_items))



def ai_parse_answer(raw: str) -> dict:
    """
    Phân tích câu trả lời của AI thành dạng có thể dùng được.
    Trả về dict: {"type": "choice"|"text"|"expression", "value": ...}
    """
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    match = re.search(r'\b([A-Da-d])\b', raw)
    if match:
        return {"type": "choice", "value": match.group(1).upper()}

    num_match = re.search(r'[-]?\d+(?:[.,]\d+)?(?:/[1-9]\d*)?', raw)
    if num_match:
        return {"type": "text", "value": num_match.group()}

    return {"type": "text", "value": raw}


# ══════════════════════════════════════════════════
#  KHAN ACADEMY HELPERS
# ══════════════════════════════════════════════════

async def take_screenshot_b64(page: Page, selector: str | None = None) -> str:
    """
    Chụp ảnh trang (hoặc element) và trả về base64.
    Khoan chụp trong 5s sau khi AI trả lời để câu hỏi mới hiển thị đầy đủ.
    """
    global LAST_AI_ANSWER_TIME
    if LAST_AI_ANSWER_TIME > 0 and AI_PAUSE_SECONDS > 0:
        elapsed = time.time() - LAST_AI_ANSWER_TIME
        if elapsed < AI_PAUSE_SECONDS:
            wait_remaining = AI_PAUSE_SECONDS - elapsed
            print(f"⏳ Khoan chụp màn hình trong {wait_remaining:.1f}s để giao diện câu mới ổn định...")
            await asyncio.sleep(wait_remaining)

    SCREENSHOT_DIR.mkdir(exist_ok=True)
    path = str(SCREENSHOT_DIR / f"shot_{int(time.time()*1000)}.png")
    if selector:
        el = page.locator(selector).first
        await el.screenshot(path=path)
    else:
        await page.screenshot(path=path, full_page=False)
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


async def login(page: Page) -> bool:
    """
    Đăng nhập vào Khan Academy.
    Trả về True nếu tài khoản ĐÃ ĐĂNG NHẬP TRƯỚC ĐÓ, False nếu vừa mới đăng nhập hoặc chưa.
    """
    print("🔐 Đang mở trang đăng nhập Khan Academy...")
    
    # Khan Academy dùng Cloudflare / Client Challenge và liên tục tải background metrics nên không dùng networkidle
    try:
        await page.goto("https://www.khanacademy.org/login", wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"⚠️ Cảnh báo tải trang: {e}")

    # Chờ trang vượt qua challenge hoặc hiển thị form đăng nhập
    print("⏳ Đang kiểm tra trạng thái tài khoản...")
    user_input = None
    for _ in range(30):
        # Kiểm tra nếu đã đăng nhập từ session trước
        if "login" not in page.url and ("khanacademy.org" in page.url):
            print("✅ Đã phát hiện tài khoản đăng nhập trước đó!")
            print(f"🚀 Tự động chuyển hướng tới: {CLASSROOM_URL}")
            try:
                await page.goto(CLASSROOM_URL, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(2)
            except Exception as e:
                print(f"⚠️ Lỗi chuyển hướng tới Classroom: {e}")
            return True

        for sel in ['#username-field', 'input[name="username"]', 'input[name="identifier"]', 'input[type="email"]']:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    user_input = el
                    break
            except Exception:
                pass
        if user_input:
            break
        await asyncio.sleep(1)

    if not user_input:
        print("⚠️ Không tìm thấy ô nhập email tự động (có thể đang vướng Captcha Cloudflare).")
        print("👉 Vui lòng đăng nhập bằng tay trên cửa sổ trình duyệt (bạn có 60 giây)...")
        try:
            await page.wait_for_url(lambda u: "login" not in u, timeout=60000)
            print("✅ Đăng nhập thủ công thành công!")
            return False
        except Exception:
            print("❌ Hết thời gian chờ đăng nhập.")
            return False

    # Điền Email / Username
    print(f"✍️ Đang nhập tài khoản: {KA_EMAIL}")
    await user_input.click()
    await user_input.fill(KA_EMAIL)
    await asyncio.sleep(0.5)

    # Ô Password
    pass_input = None
    for sel in ['#current-password-field', 'input[name="current-password"]', 'input[name="password"]', 'input[type="password"]']:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                pass_input = el
                break
        except Exception:
            pass

    if pass_input:
        print("🔑 Đang nhập mật khẩu...")
        await pass_input.click()
        await pass_input.fill(KA_PASSWORD)
        await asyncio.sleep(0.5)
        
        # Nhấn nút đăng nhập hoặc Enter
        submit_btn = page.locator('button[type="submit"], button[data-test-id="log-in-button"]').first
        if await submit_btn.count() > 0 and await submit_btn.is_visible():
            await submit_btn.click()
        else:
            await pass_input.press("Enter")
    else:
        # Một số giao diện yêu cầu nhấn Next sau khi nhập email rồi mới hiện password
        await user_input.press("Enter")
        await asyncio.sleep(2)
        for sel in ['#current-password-field', 'input[name="current-password"]', 'input[name="password"]', 'input[type="password"]']:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    pass_input = el
                    break
            except Exception:
                pass
        if pass_input:
            print("🔑 Đang nhập mật khẩu...")
            await pass_input.click()
            await pass_input.fill(KA_PASSWORD)
            await asyncio.sleep(0.5)
            await pass_input.press("Enter")

    print("⏳ Đang chờ hoàn tất đăng nhập...")
    try:
        await page.wait_for_url(lambda u: "login" not in u, timeout=30000)
        print("✅ Đăng nhập thành công!")
    except Exception:
        print("ℹ️ Tiếp tục tiến trình (hãy kiểm tra xem đã vào được tài khoản chưa).")

    return False



async def detect_exercise_type(page: Page) -> str:
    # Kiểm tra các loại widget bài tập (ưu tiên tìm trong khu vực bài tập perseus)
    container = page.locator('article, [data-testid="exercise-content"], .framework-perseus, main').first
    scope = container if await container.count() > 0 else page

    # Matcher / Orderer / Sorter (Kéo thả, ghép nối, sắp xếp)
    matcher_selectors = [
        '[data-perseus-widget-type="matcher"]',
        '.perseus-widget-matcher',
        '[data-perseus-widget-type="orderer"]',
        '.perseus-widget-orderer',
        '[data-perseus-widget-type="sorter"]',
        '.perseus-widget-sorter',
        '[data-widget-type="matcher"]',
        '[data-widget-type="orderer"]',
        '[data-widget-type="sorter"]',
        '.orderer-card',
        '[class*="orderer-card"]',
        '[class*="orderer-item"]',
        '[class*="sortable-card"]',
        '[class*="sortable-item"]',
        '[aria-roledescription="sortable"]',
        '[draggable="true"]',
    ]
    for sel in matcher_selectors:
        try:
            if await scope.locator(sel).count() > 0:
                return "matcher"
        except Exception:
            pass

    # Kiểm tra thêm qua cấu trúc bảng ghép nối (Khái niệm / Phương án)
    try:
        has_matcher_table = await scope.evaluate("""(root) => {
            const tables = Array.from(root.querySelectorAll('table, [class*="table"], [class*="matcher"]'));
            for (const t of tables) {
                const rows = Array.from(t.querySelectorAll('tr')).filter(r => r.querySelectorAll('td').length >= 2);
                if (rows.length >= 2) return true;
            }
            return false;
        }""")
        if has_matcher_table:
            return "matcher"
    except Exception:
        pass

    # Multiple choice
    mc_selectors = [
        '[data-test-id="choice-icon"]',
        '.perseus-radio-option',
        '[class*="radio-option"]',
        'ul[class*="perseus-widget-radio"]',
        '[role="radio"]',
    ]
    for sel in mc_selectors:
        try:
            if await scope.locator(sel).count() > 0:
                return "multiple_choice"
        except Exception:
            pass

    # Expression editor / MathQuill / Keypad (Toán học biểu thức hoặc phương trình)
    expr_selectors = [
        '.mq-editable-field',
        '.mq-math-mode',
        '.perseus-widget-expression',
        '[data-widget-type="expression"]',
        '[class*="expression-editor"]',
        '[class*="expression"]',
        '.math-input',
        '[data-testid*="math-input"]',
        '[data-testid*="keypad-input"]',
        '[class*="keypad-input"]',
        '.perseus-math-input',
        'button[data-testid*="keypad"]',
        'button[aria-label*="keypad"]',
        'button[aria-label*="Math keys"]',
        '[aria-label*="Math input"]',
        '[aria-label*="math input"]',
    ]
    for sel in expr_selectors:
        try:
            if await scope.locator(sel).count() > 0:
                return "expression"
        except Exception:
            pass

    # Input answer (loại trừ input type="range" như thanh resize pane)
    input_selectors = [
        'input.perseus-input-size-normal',
        'input[class*="perseus"]:not([type="range"])',
        '.perseus-input input:not([type="range"])',
        '.framework-perseus input:not([type="range"])',
        'article input:not([type="range"])',
    ]
    for sel in input_selectors:
        try:
            if await scope.locator(sel).count() > 0:
                return "input"
        except Exception:
            pass

    return "unknown"


# ──────────────────────────────────────────────────
#  HANDLERS cho từng loại bài tập
# ──────────────────────────────────────────────────

async def find_exercise_choices(page: Page):
    """
    Tìm tất cả các lựa chọn trắc nghiệm trong câu hỏi Khan Academy.
    Hỗ trợ mọi định dạng: role="radio", role="checkbox", ul > li, label, WonderBlocks.
    """
    container = page.locator('article, [data-testid="exercise-content"], .framework-perseus, main').first
    scope = container if await container.count() > 0 else page

    candidate_selectors = [
        '[role="radio"]',
        '[role="checkbox"]',
        'ul[data-testid*="radio"] > li',
        'ul[data-testid*="choice"] > li',
        'ul[class*="radio"] > li',
        '[data-testid*="radio-option"]',
        '[data-testid*="choice-icon"]',
        '[data-test-id="choice-icon"]',
        '.perseus-radio-option',
        'li[class*="choice"]',
        'fieldset label',
        'fieldset ul > li',
    ]

    for sel in candidate_selectors:
        els = scope.locator(sel)
        cnt = await els.count()
        if cnt >= 2:
            visible_els = []
            for i in range(cnt):
                item = els.nth(i)
                try:
                    if await item.is_visible():
                        visible_els.append(item)
                except Exception:
                    pass
            if len(visible_els) >= 2:
                return visible_els

    # Fallback tìm trong ul có số lượng li từ 2 đến 8
    ul_els = scope.locator('ul')
    for u_idx in range(await ul_els.count()):
        u = ul_els.nth(u_idx)
        lis = u.locator('> li')
        cnt = await lis.count()
        if 2 <= cnt <= 8:
            return [lis.nth(i) for i in range(cnt)]

    return []


async def handle_multiple_choice(page: Page) -> bool:
    """Xử lý câu hỏi trắc nghiệm."""
    print("📝 Loại: Trắc nghiệm (multiple choice)")

    # 1. Tìm các lựa chọn trắc nghiệm
    choices = await find_exercise_choices(page)
    num_choices = len(choices)
    print(f"🔍 Phát hiện {num_choices} lựa chọn trắc nghiệm.")

    # Lấy nội dung câu hỏi
    question_text = ""
    for sel in ['[class*="problem-title"]', '.perseus-problem', '[data-testid="exercise-content"]',
                '.framework-perseus', 'article']:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                question_text = await el.inner_text()
                break
        except Exception:
            pass

    # Lấy danh sách text của các lựa chọn
    choice_texts = []
    for i, ch in enumerate(choices):
        try:
            txt = (await ch.inner_text()).strip()
            txt = re.sub(r'\s+', ' ', txt)
            choice_texts.append(f"{chr(65+i)}) {txt}")
        except Exception:
            choice_texts.append(f"{chr(65+i)}) (Lựa chọn {chr(65+i)})")

    img_b64 = await take_screenshot_b64(page)

    choices_str = "\n".join(choice_texts) if choice_texts else "(xem trong ảnh đính kèm)"
    prompt = f"""
Bạn đang xem một câu hỏi trắc nghiệm từ Khan Academy.

Câu hỏi:
{question_text if question_text else "(xem ảnh chụp màn hình)"}

Các lựa chọn:
{choices_str}

Hãy suy nghĩ giải từng bước để tìm ra đáp án đúng.
Ở dòng cuối cùng của câu trả lời, hãy ghi rõ chữ cái đáp án đúng (ví dụ A, B, C hoặc D):
FINAL_CHOICE: B
""".strip()

    answer_raw = ai_ask(prompt, img_b64)
    print(f"🤖 AI trả lời:\n{answer_raw}")

    # Tìm chữ cái đáp án
    target_idx = None

    # Cách 1: FINAL_CHOICE: B
    m_final = re.search(r'FINAL_CHOICE\s*:\s*([A-E])', answer_raw, re.IGNORECASE)
    if m_final:
        target_idx = ord(m_final.group(1).upper()) - ord('A')

    # Cách 2: Tìm chữ cái A/B/C/D độc lập
    if target_idx is None:
        m_let = re.search(r'(?:^|[^\w])([A-E])(?:[.):\s]|$)', answer_raw)
        if m_let:
            target_idx = ord(m_let.group(1).upper()) - ord('A')

    # Cách 3: So khớp nội dung câu trả lời với text của từng lựa chọn
    if target_idx is None or target_idx >= num_choices:
        for idx, txt in enumerate(choice_texts):
            clean_txt = re.sub(r'^[A-E]\)\s*', '', txt).strip().lower()
            if clean_txt and (clean_txt in answer_raw.lower() or answer_raw.lower() in clean_txt):
                target_idx = idx
                break

    # Nếu vẫn không tìm được hoặc vượt giới hạn -> mặc định chọn A
    if target_idx is None or target_idx >= num_choices:
        target_idx = 0

    if choices and 0 <= target_idx < len(choices):
        target_choice = choices[target_idx]
        print(f"👉 Đang bấm chọn đáp án {chr(65+target_idx)}...")
        try:
            await target_choice.scroll_into_view_if_needed()
            await asyncio.sleep(0.1)
            await target_choice.click(timeout=3000, force=True)
            print(f"✅ Đã chọn thành công đáp án {chr(65+target_idx)}!")
            return True
        except Exception as e:
            print(f"⚠️ Thử click selector con trong lựa chọn: {e}")
            try:
                sub = target_choice.locator('input, [role="radio"], [role="checkbox"], svg, span').first
                await sub.click(timeout=3000, force=True)
                print(f"✅ Đã chọn đáp án {chr(65+target_idx)} qua phần tử con!")
                return True
            except Exception:
                pass

    print("⚠️ Không thể chọn được đáp án trắc nghiệm.")
    return False


async def find_exercise_inputs(page: Page):
    """
    Tìm tất cả các ô input điền đáp án trong bài tập (bao gồm ô input HTML chuẩn,
    ô MathQuill, ô expression editor có bàn phím ảo keypad),
    sắp xếp theo thứ tự từ trên xuống dưới (theo tọa độ y).
    """
    container = page.locator('article, [data-testid="exercise-content"], .framework-perseus, main').first
    scope = container if await container.count() > 0 else page

    input_selectors = [
        # 1. Các widget biểu thức toán học / MathQuill / Keypad (ưu tiên hàng đầu)
        '.mq-editable-field',
        '[data-testid*="keypad-input"]',
        '[data-testid*="math-input"]',
        '[class*="expression-editor"]',
        '.perseus-widget-expression',
        '[data-widget-type="expression"]',
        '.perseus-math-input',
        # 2. Các ô input HTML thông thường
        'input.perseus-input-size-normal',
        'input[class*="perseus"]:not([type="range"])',
        '.perseus-input input:not([type="range"])',
        'input[type="text"]:not([type="range"])',
        'input[type="number"]:not([type="range"])',
        'input:not([type="range"]):not([type="hidden"])',
    ]

    elements_with_pos = []
    for sel in input_selectors:
        try:
            els = scope.locator(sel)
            cnt = await els.count()
        except Exception:
            continue

        for i in range(cnt):
            inp = els.nth(i)
            try:
                if not await inp.is_visible():
                    continue

                # Bỏ qua thanh trượt resize pane
                t_type = await inp.get_attribute("type")
                if t_type == "range":
                    continue
                aria_lbl = (await inp.get_attribute("aria-label") or "").lower()
                if "resize" in aria_lbl or "split" in aria_lbl:
                    continue

                box = await inp.bounding_box()
                if not box or box["width"] <= 15 or box["height"] <= 15:
                    continue

                box_center_x = box["x"] + box["width"] / 2.0
                box_center_y = box["y"] + box["height"] / 2.0
                box_area = box["width"] * box["height"]

                duplicate_found = False
                for idx, (existing_el, existing_box) in enumerate(elements_with_pos):
                    ex_center_x = existing_box["x"] + existing_box["width"] / 2.0
                    ex_center_y = existing_box["y"] + existing_box["height"] / 2.0
                    ex_area = existing_box["width"] * existing_box["height"]

                    # Trùng nếu cùng hàng/vị trí tương đương hoặc một hộp chứa hộp kia
                    is_same_pos = abs(box_center_y - ex_center_y) < 25 and abs(box_center_x - ex_center_x) < 60
                    box_contains_ex = (
                        box["x"] <= existing_box["x"] + 10 and
                        box["y"] <= existing_box["y"] + 10 and
                        (box["x"] + box["width"]) >= (existing_box["x"] + existing_box["width"] - 10) and
                        (box["y"] + box["height"]) >= (existing_box["y"] + existing_box["height"] - 10)
                    )
                    ex_contains_box = (
                        existing_box["x"] <= box["x"] + 10 and
                        existing_box["y"] <= box["y"] + 10 and
                        (existing_box["x"] + existing_box["width"]) >= (box["x"] + box["width"] - 10) and
                        (existing_box["y"] + existing_box["height"]) >= (box["y"] + box["height"] - 10)
                    )

                    if is_same_pos or box_contains_ex or ex_contains_box:
                        duplicate_found = True
                        # Ưu tiên phần tử con có diện tích nhỏ hơn/chính xác hơn (ví dụ .mq-editable-field trong container)
                        if box_area < ex_area:
                            elements_with_pos[idx] = (inp, box)
                        break

                if not duplicate_found:
                    elements_with_pos.append((inp, box))
            except Exception:
                continue

    # Sắp xếp theo chiều dọc (từ trên xuống dưới)
    elements_with_pos.sort(key=lambda item: (round(item[1]["y"]), round(item[1]["x"])))
    return [item[0] for item in elements_with_pos]


async def fill_input_safely(page: Page, target, value: str) -> bool:
    """
    Điền giá trị vào ô input / MathQuill theo cơ chế mô phỏng gõ phím người dùng thật.
    Hỗ trợ cả ô <input> chuẩn (kích hoạt React SyntheticEvent) và ô MathQuill / keypad.
    """
    val_str = str(value).strip()
    # Chuẩn hóa các dấu trừ và gạch ngang Unicode về dấu trừ ASCII chuẩn
    val_str = val_str.replace('−', '-').replace('–', '-').replace('—', '-')

    try:
        # 1. Cuộn tới phần tử
        await target.scroll_into_view_if_needed()
        await asyncio.sleep(0.05)

        # 2. Kiểm tra loại phần tử (input thường hay MathQuill / div / span)
        tag_name = await target.evaluate("el => el.tagName.toLowerCase()")
        is_standard_input = tag_name in ("input", "textarea")

        # Nếu là container MathQuill, click vào .mq-editable-field bên trong để tránh click trúng nút keypad
        click_target = target
        if not is_standard_input:
            inner_mq = target.locator('.mq-editable-field').first
            if await inner_mq.count() > 0 and await inner_mq.is_visible():
                click_target = inner_mq

        # Click để focus con trỏ vào ô nhập
        await click_target.click(timeout=3000, force=True)
        await asyncio.sleep(0.1)

        # Nếu có textarea bên trong MathQuill, focus thẳng vào nó
        try:
            mq_textarea = click_target.locator('textarea').first
            if await mq_textarea.count() > 0:
                await mq_textarea.focus(timeout=1000)
        except Exception:
            pass

        # 3. Xóa sạch dữ liệu cũ
        await page.keyboard.press("Control+A")
        await asyncio.sleep(0.04)
        await page.keyboard.press("Backspace")
        await asyncio.sleep(0.04)

        # 4. Gõ phím từng ký tự (mô phỏng người dùng thật)
        await page.keyboard.type(val_str, delay=45)
        await asyncio.sleep(0.1)

        # 5. Đồng bộ sự kiện input/change cho React và MathQuill
        if is_standard_input:
            await target.evaluate("""(element, val) => {
                try {
                    const proto = element.tagName.toLowerCase() === 'textarea' 
                        ? window.HTMLTextAreaElement.prototype 
                        : window.HTMLInputElement.prototype;
                    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (descriptor && descriptor.set) {
                        descriptor.set.call(element, val);
                    } else {
                        element.value = val;
                    }
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                } catch (e) {}
            }""", val_str)
        else:
            await click_target.evaluate("""(element) => {
                try {
                    const ta = element.querySelector('textarea') || (element.tagName.toLowerCase() === 'textarea' ? element : null);
                    if (ta) {
                        ta.dispatchEvent(new Event('input', { bubbles: true }));
                        ta.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                } catch (e) {}
            }""")

        # 6. Nhấn Tab để hoàn tất nhập và kích hoạt sự kiện blur/change
        await page.keyboard.press("Tab")
        await asyncio.sleep(0.08)
        return True
    except Exception as e:
        print(f"⚠️ Thử điền fallback cho ô: {e}")
        try:
            await target.fill(val_str, timeout=3000)
            return True
        except Exception:
            try:
                await target.click(force=True)
                await page.keyboard.type(val_str, delay=50)
                return True
            except Exception:
                return False


async def handle_input_answer(page: Page) -> bool:
    """Xử lý bài điền đáp án (hỗ trợ cả bài có 1 ô hoặc nhiều ô như tìm x, y, hoặc phương trình)."""
    print("✏️  Loại: Điền đáp án (input)")

    # 1. Tìm tất cả các ô input hiện có trong bài tập
    valid_inputs = await find_exercise_inputs(page)
    num_inputs = len(valid_inputs)
    print(f"🔍 Phát hiện {num_inputs} ô điền đáp án trên trang.")

    if num_inputs == 0:
        print("⚠️  Không tìm thấy ô input nào thích hợp.")
        return False

    question_text = ""
    for sel in ['.perseus-problem', '[class*="problem"]', 'article', '.framework-perseus', '[data-testid="exercise-content"]']:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                question_text = await el.inner_text()
                break
        except Exception:
            pass

    img_b64 = await take_screenshot_b64(page)

    prompt = f"""
Bạn đang xem một bài tập Khan Academy yêu cầu điền đáp án.

Nội dung bài tập:
{question_text if question_text else "(xem ảnh chụp màn hình)"}

LƯU Ý ĐẶC BIỆT:
Bài toán này có đúng {num_inputs} ô cần điền đáp án (sắp xếp theo thứ tự từ trên xuống dưới).
- Ví dụ nếu là hệ phương trình thì ô 1 là x, ô 2 là y.
- Nếu là bài toán kết hợp phương trình (combining equations) thì đáp án là phương trình thu được (ví dụ -5x = 8).
Hãy giải bài toán cẩn thận từng bước để không bị nhầm dấu hay sai số.
Ở dòng cuối cùng của câu trả lời, HÃY GHI ĐÁP ÁN THEO ĐỊNH DẠNG SAU:
FINAL_ANSWERS: [đáp_án]

Ví dụ:
FINAL_ANSWERS: [7, 0]
hoặc nếu chỉ có 1 ô:
FINAL_ANSWERS: [12]
hoặc nếu đáp án là phương trình:
FINAL_ANSWERS: [-5x = 8]
""".strip()

    answer_raw = ai_ask(prompt, img_b64)
    print(f"🤖 AI trả lời:\n{answer_raw}")

    # Trích xuất danh sách đáp án
    answers = parse_multi_answers(answer_raw, expected_count=num_inputs)
    print(f"📋 Danh sách đáp án trích xuất được: {answers}")

    # Điền lần lượt vào từng ô input
    for idx, inp in enumerate(valid_inputs):
        if idx < len(answers):
            val = answers[idx]
        else:
            val = answers[-1]

        success = await fill_input_safely(page, inp, val)
        if success:
            print(f"✅ Đã điền ô {idx+1}/{num_inputs}: {val}")
        else:
            print(f"❌ Không thể điền vào ô {idx+1}/{num_inputs}")

    return True


async def handle_expression(page: Page) -> bool:
    """Xử lý bài nhập biểu thức toán học (MathQuill / Keypad / Expression editor)."""
    print("🔢 Loại: Biểu thức toán học (expression)")

    # Tìm các ô nhập biểu thức
    valid_inputs = await find_exercise_inputs(page)
    num_inputs = max(len(valid_inputs), 1)
    print(f"🔍 Phát hiện {len(valid_inputs)} ô nhập biểu thức trên trang.")

    if not valid_inputs:
        print("⚠️ Không tìm thấy ô nhập biểu thức nào!")
        return False

    img_b64 = await take_screenshot_b64(page)
    question_text = ""
    for sel in ['.perseus-problem', '[class*="problem"]', 'article', '.framework-perseus', '[data-testid="exercise-content"]']:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                question_text = await el.inner_text()
                break
        except Exception:
            pass

    prompt = f"""
Bạn đang xem một bài tập Khan Academy yêu cầu nhập biểu thức toán học hoặc phương trình.

Nội dung bài tập:
{question_text if question_text else "(xem ảnh chụp màn hình)"}

LƯU Ý QUAN TRỌNG:
- Có đúng {num_inputs} ô cần nhập trên màn hình.
- Nếu là bài toán kết hợp phương trình (combining equations), đáp án là phương trình thu được khi thực hiện phép tính (ví dụ trừ hai phương trình).
- Hãy giải bài toán cẩn thận từng bước để không bị nhầm dấu hay sai hệ số.
- Ở dòng cuối cùng của câu trả lời, hãy ghi đáp án theo đúng định dạng sau:
FINAL_ANSWERS: [đáp_án]

Ví dụ:
- Nếu đáp án là phương trình:
FINAL_ANSWERS: [-5x = 8]
- Nếu đáp án là số hoặc phân số:
FINAL_ANSWERS: [12]
hoặc
FINAL_ANSWERS: [-3/4]
- Nếu có {num_inputs} ô (ví dụ x và y):
FINAL_ANSWERS: [đáp_án_1, đáp_án_2]
Chỉ ghi đúng giá trị/biểu thức cần điền vào trong ngoặc vuông, không thêm chữ thừa.
""".strip()

    answer_raw = ai_ask(prompt, img_b64)
    print(f"🤖 AI trả lời:\n{answer_raw}")

    answers = parse_multi_answers(answer_raw, expected_count=num_inputs)
    print(f"📋 Danh sách đáp án biểu thức trích xuất được: {answers}")

    for idx, inp in enumerate(valid_inputs):
        val = answers[idx] if idx < len(answers) else answers[-1]
        success = await fill_input_safely(page, inp, val)
        if success:
            print(f"✅ Đã điền ô biểu thức {idx+1}/{num_inputs}: {val}")
        else:
            print(f"❌ Không thể điền vào ô biểu thức {idx+1}/{num_inputs}")

    return True


async def find_matcher_pairs(page: Page) -> list:
    """
    Tìm các cặp ghép nối hoặc danh sách các thẻ cần sắp xếp (Matcher / Orderer / Sorter).
    Gán attribute data-ai-card-idx và data-ai-slot-idx để dễ dàng thao tác kéo thả.
    Trả về danh sách dict: [{'index': 0, 'leftText': '...', 'rightText': '...'}, ...]
    """
    try:
        pairs = await page.evaluate("""() => {
            const root = document.querySelector('article, [data-testid="exercise-content"], .framework-perseus, main') || document.body;
            const widget = root.querySelector('.perseus-widget-matcher, [data-perseus-widget-type="matcher"], [class*="matcher"], .perseus-widget-orderer, [data-perseus-widget-type="orderer"], [class*="orderer"]') || root;

            // Xóa các tag cũ nếu có
            document.querySelectorAll('[data-ai-card-idx]').forEach(el => el.removeAttribute('data-ai-card-idx'));
            document.querySelectorAll('[data-ai-slot-idx]').forEach(el => el.removeAttribute('data-ai-slot-idx'));

            function getMathText(el) {
                if (!el) return '';
                // Thử lấy chuỗi LaTeX nguyên bản từ KaTeX annotation
                const annotation = el.querySelector('annotation');
                if (annotation && annotation.textContent) {
                    return annotation.textContent.trim();
                }
                return (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
            }

            // Cách 1: Thử tìm cấu trúc bảng (table / tbody / tr)
            const rows = Array.from(widget.querySelectorAll('tbody tr, tr')).filter(r => r.querySelectorAll('td').length >= 2);
            if (rows.length >= 2) {
                const result = [];
                rows.forEach((row, i) => {
                    const tds = row.querySelectorAll('td');
                    const leftEl = tds[0];
                    const rightTd = tds[1];
                    const cardEl = rightTd.querySelector('.orderer-card, [class*="orderer-card"], [class*="orderer-item"], [class*="card"], [class*="box"], [class*="draggable"], [draggable="true"], div') || rightTd;

                    cardEl.setAttribute('data-ai-card-idx', i.toString());
                    rightTd.setAttribute('data-ai-slot-idx', i.toString());

                    result.push({
                        index: i,
                        leftText: getMathText(leftEl),
                        rightText: getMathText(cardEl)
                    });
                });
                return result;
            }

            // Cách 2: Thử tìm các thẻ orderer / sortable card trực tiếp
            const cards = Array.from(widget.querySelectorAll('.orderer-card, [class*="orderer-card"], [class*="orderer-item"], [class*="sortable-card"], [class*="draggable"], [draggable="true"], [aria-roledescription="sortable"]'));
            if (cards.length >= 2) {
                const result = [];
                cards.forEach((c, i) => {
                    c.setAttribute('data-ai-card-idx', i.toString());
                    c.setAttribute('data-ai-slot-idx', i.toString());
                    result.push({
                        index: i,
                        leftText: `Vị trí ${i + 1}`,
                        rightText: getMathText(c)
                    });
                });
                return result;
            }

            // Cách 3: Tìm các khung div có border và border-radius (các ô đáp án kéo thả)
            const allDivs = Array.from(root.querySelectorAll('div, li'));
            const bordered = allDivs.filter(el => {
                const style = window.getComputedStyle(el);
                const hasBorder = style.borderWidth !== '0px' && style.borderColor !== 'transparent';
                const hasRadius = parseInt(style.borderRadius, 10) >= 3;
                const rect = el.getBoundingClientRect();
                return hasBorder && hasRadius && rect.width > 50 && rect.height > 25 && rect.height < 250;
            });

            if (bordered.length >= 2) {
                bordered.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
                const result = [];
                bordered.forEach((c, i) => {
                    c.setAttribute('data-ai-card-idx', i.toString());
                    c.setAttribute('data-ai-slot-idx', i.toString());
                    result.push({
                        index: i,
                        leftText: `Vị trí ${i + 1}`,
                        rightText: getMathText(c)
                    });
                });
                return result;
            }

            return [];
        }""")
        return pairs or []
    except Exception as e:
        print(f"⚠️ Lỗi khi quét matcher pairs: {e}")
        return []


async def perform_card_drag(page: Page, curr_slot: int, target_slot: int) -> bool:
    """
    Kéo thả thẻ từ vị trí hiện tại curr_slot đến vị trí mong muốn target_slot.
    Tự động tính toán hướng di chuyển (lên hoặc xuống) và di chuyển mượt bằng chuột thực.
    """
    if curr_slot == target_slot:
        return True

    coords = await page.evaluate("""([currSlot, targetSlot]) => {
        let cards = Array.from(document.querySelectorAll('[data-ai-card-idx]'));
        if (cards.length === 0) {
            cards = Array.from(document.querySelectorAll('.orderer-card, [class*="orderer-card"], [class*="orderer-item"], [class*="draggable"], [draggable="true"]'));
        }
        if (cards.length === 0) return null;

        const isHorizontal = cards.length >= 2 && Math.abs(cards[0].getBoundingClientRect().top - cards[1].getBoundingClientRect().top) < 15;

        if (isHorizontal) {
            cards.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
        } else {
            cards.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
        }

        if (currSlot >= cards.length || targetSlot >= cards.length) return null;

        const srcCard = cards[currSlot];
        const tgtCard = cards[targetSlot];

        srcCard.scrollIntoView({ behavior: 'instant', block: 'center' });

        const srcRect = srcCard.getBoundingClientRect();
        const tgtRect = tgtCard.getBoundingClientRect();

        let tgtX, tgtY;
        if (isHorizontal) {
            tgtY = tgtRect.top + tgtRect.height / 2;
            tgtX = currSlot > targetSlot ? (tgtRect.left + tgtRect.width * 0.2) : (tgtRect.right - tgtRect.width * 0.2);
        } else {
            tgtX = tgtRect.left + tgtRect.width / 2;
            tgtY = currSlot > targetSlot ? (tgtRect.top + tgtRect.height * 0.2) : (tgtRect.bottom - tgtRect.height * 0.2);
        }

        return {
            src: { x: srcRect.left + srcRect.width / 2, y: srcRect.top + srcRect.height / 2 },
            tgt: { x: tgtX, y: tgtY }
        };
    }""", [curr_slot, target_slot])

    if not coords:
        print(f"⚠️ Không lấy được tọa độ kéo thả giữa {curr_slot} và {target_slot}")
        return False

    src_x = coords["src"]["x"]
    src_y = coords["src"]["y"]
    tgt_x = coords["tgt"]["x"]
    tgt_y = coords["tgt"]["y"]

    print(f"🔄 Kéo thẻ: ({src_x:.0f}, {src_y:.0f}) -> ({tgt_x:.0f}, {tgt_y:.0f})...")

    # 1. Thử chuột mô phỏng tự nhiên (OS-level mouse drag)
    try:
        await page.mouse.move(src_x, src_y)
        await asyncio.sleep(0.12)
        await page.mouse.down()
        await asyncio.sleep(0.15)
        # Di chuyển theo 35 bước để kích hoạt các sự kiện dragover / mousemove
        await page.mouse.move(tgt_x, tgt_y, steps=35)
        await asyncio.sleep(0.2)
        # Di chuyển nhẹ +/- 2px tại điểm đích để kích hoạt sự kiện hover
        await page.mouse.move(tgt_x, tgt_y + (2 if curr_slot > target_slot else -2), steps=5)
        await asyncio.sleep(0.15)
        await page.mouse.up()
        await asyncio.sleep(0.6)
        return True
    except Exception as e:
        print(f"⚠️ Lỗi chuột drag: {e}")

    # 2. Thử HTML5 drag event dispatch qua evaluate fallback
    try:
        await page.evaluate("""([currSlot, targetSlot]) => {
            let cards = Array.from(document.querySelectorAll('[data-ai-card-idx]'));
            if (cards.length === 0) {
                cards = Array.from(document.querySelectorAll('.orderer-card, [class*="orderer-card"], [class*="orderer-item"], [class*="draggable"], [draggable="true"]'));
            }
            cards.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
            const src = cards[currSlot];
            const dst = cards[targetSlot];
            if (!src || !dst) return;

            const dt = new DataTransfer();
            src.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt }));
            dst.dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: dt }));
            dst.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt }));
            dst.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
            src.dispatchEvent(new DragEvent('dragend', { bubbles: true, cancelable: true, dataTransfer: dt }));
        }""", [curr_slot, target_slot])
        await asyncio.sleep(0.5)
        return True
    except Exception as e:
        print(f"⚠️ Lỗi HTML5 drag: {e}")

    return False


async def handle_matcher_answer(page: Page) -> bool:
    """
    Xử lý bài tập ghép nối / kéo thả / sắp xếp (Matcher / Orderer / Sorter).
    AI phân tích câu hỏi và thứ tự ghép đúng, sau đó tự động kéo thả từng thẻ vào đúng vị trí.
    """
    print("🧩 Loại: Kéo thả / Ghép nối / Sắp xếp (Matcher / Orderer)")

    pairs = await find_matcher_pairs(page)
    num_items = len(pairs)
    print(f"🔍 Phát hiện {num_items} mục cần ghép nối / sắp xếp.")

    if num_items < 2:
        print("⚠️ Không đủ số mục để thực hiện ghép nối.")
        return False

    # Lấy nội dung câu hỏi
    question_text = ""
    for sel in ['[class*="problem-title"]', '.perseus-problem', '[data-testid="exercise-content"]',
                '.framework-perseus', 'article']:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                question_text = await el.inner_text()
                break
        except Exception:
            pass

    # Xây dựng danh sách khái niệm (cột trái) và thẻ hiện tại (cột phải)
    left_lines = []
    right_lines = []
    for p in pairs:
        idx = p['index'] + 1
        left_lines.append(f"Vị trí {idx}: {p['leftText']}")
        right_lines.append(f"Thẻ [{idx}]: {p['rightText']}")

    left_str = "\n".join(left_lines)
    right_str = "\n".join(right_lines)

    img_b64 = await take_screenshot_b64(page)

    prompt = f"""
Bạn đang xem một bài tập dạng NỐI / SẮP XẾP / KÉO THẢ (Matcher / Orderer) từ Khan Academy.

Câu hỏi:
{question_text if question_text else "(xem trong ảnh đính kèm)"}

Các vị trí / khái niệm tương ứng từ trên xuống dưới:
{left_str}

Các thẻ hiện tại ở cột đáp án:
{right_str}

Hãy phân tích kỹ đề bài và hình ảnh:
1. Xác định mỗi vị trí ở cột trái cần ghép với Thẻ nào ở cột phải (Thẻ [1], Thẻ [2], Thẻ [3]...).
2. Đưa ra thứ tự đúng của các thẻ (đánh số 1, 2, 3... theo số thứ tự ban đầu của các thẻ) cần đặt từ trên xuống dưới.

Ví dụ:
Nếu Thẻ [2] phải ở vị trí 1, Thẻ [1] phải ở vị trí 2, Thẻ [3] phải ở vị trí 3:
FINAL_ORDER: [2, 1, 3]

Ở dòng cuối cùng của câu trả lời, HÃY GHI RÕ:
FINAL_ORDER: [danh sách các số thứ tự thẻ từ trên xuống dưới]
""".strip()

    answer_raw = ai_ask(prompt, img_b64)
    print(f"🤖 AI trả lời:\n{answer_raw}")

    # Parse thứ tự mục tiêu (0-indexed)
    target_order = parse_order_answers(answer_raw, num_items)
    print(f"📋 Thứ tự thẻ mong muốn (1-indexed): {[x + 1 for x in target_order]}")

    # Trạng thái vị trí hiện tại của các thẻ: ban đầu là [0, 1, ..., num_items - 1]
    current_state = list(range(num_items))

    # Thực hiện các bước kéo thả để đưa từng thẻ về đúng vị trí mong muốn
    for target_slot in range(num_items):
        target_card = target_order[target_slot]
        curr_slot = current_state.index(target_card)

        if curr_slot != target_slot:
            print(f"👉 Kéo Thẻ [{target_card + 1}] từ vị trí {curr_slot + 1} -> vị trí {target_slot + 1}...")
            success = await perform_card_drag(page, curr_slot, target_slot)
            if success:
                moved_card = current_state.pop(curr_slot)
                current_state.insert(target_slot, moved_card)
                await asyncio.sleep(0.4)

    print("✨ Đã hoàn thành quá trình kéo thả sắp xếp các thẻ!")
    return True


async def handle_unknown(page: Page) -> bool:
    """Xử lý bài tập không xác định — quét tìm ô input/expression, trắc nghiệm hoặc kéo thả trước khi fallback."""
    print("❓ Loại: Không xác định — đang quét tìm ô nhập liệu, lựa chọn hoặc kéo thả...")

    # 1. Thử tìm ô input hoặc biểu thức
    inputs = await find_exercise_inputs(page)
    if inputs:
        print(f"💡 Phát hiện {len(inputs)} ô nhập liệu, chuyển sang xử lý biểu thức / input!")
        return await handle_expression(page)

    # 2. Thử tìm lựa chọn trắc nghiệm
    choices = await find_exercise_choices(page)
    if choices:
        print(f"💡 Phát hiện {len(choices)} lựa chọn trắc nghiệm, chuyển sang xử lý trắc nghiệm!")
        return await handle_multiple_choice(page)

    # 3. Thử tìm bài tập ghép nối / kéo thả (Matcher / Orderer)
    matcher_pairs = await find_matcher_pairs(page)
    if matcher_pairs and len(matcher_pairs) >= 2:
        print(f"💡 Phát hiện bài tập kéo thả / ghép nối ({len(matcher_pairs)} mục), chuyển sang xử lý matcher!")
        return await handle_matcher_answer(page)

    # 4. Fallback chụp ảnh hỏi AI
    img_b64 = await take_screenshot_b64(page)
    prompt = """
Đây là ảnh chụp màn hình một bài tập từ Khan Academy.
Hãy phân tích bài tập và cho biết:
1. Loại câu hỏi là gì?
2. Đáp án là gì?
3. Cần làm gì để trả lời (click lựa chọn nào, điền gì, v.v.)?

Trả lời ngắn gọn.
""".strip()

    answer_raw = ai_ask(prompt, img_b64)
    print(f"🤖 AI phân tích: {answer_raw}")
    return False


# ──────────────────────────────────────────────────
#  NÚT SUBMIT / CHECK ANSWER
# ──────────────────────────────────────────────────

async def click_check_answer_button(page: Page) -> bool:
    """
    Tự động tìm và nhấn nút 'Kiểm tra' (Check / Submit) sau khi hoàn thành bài làm.
    Đảm bảo chờ nút enabled, dùng force=True và JavaScript click fallback.
    """
    print("👉 Đang tự động nhấn nút Kiểm tra (Check)...")

    check_selectors = [
        'button[data-testid="check-answer-button"]',
        'button[data-testid*="check-answer"]',
        'button[data-testid*="check"]',
        'button[data-test-id="check-answer-button"]',
        'button[data-test-id*="check"]',
        'button:has-text("Kiểm tra kết quả")',
        'button:has-text("Kiểm tra câu trả lời")',
        'button:has-text("Kiểm tra")',
        'button:has-text("Check answer")',
        'button:has-text("Check results")',
        'button:has-text("Check")',
        'button:has-text("Submit")',
        'button[aria-label*="Check" i]',
        'button[aria-label*="Kiểm tra" i]',
        '[data-testid="question-bottom-nav"] button',
        '[data-testid*="bottom-nav"] button',
        'footer button:has-text("Kiểm tra")',
        'footer button:has-text("Check")',
        '[class*="check-answer"] button',
        'button[class*="check-answer"]',
    ]

    for attempt in range(6):
        for sel in check_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    # Chờ nút enabled nếu React đang re-render
                    if not await btn.is_enabled():
                        await asyncio.sleep(0.2)
                        if not await btn.is_enabled():
                            continue

                    await btn.scroll_into_view_if_needed()
                    await asyncio.sleep(0.1)
                    try:
                        await btn.click(timeout=2500, force=True)
                    except Exception:
                        await btn.evaluate("el => el.click()")

                    print(f"🖱️  ✅ Đã tự động nhấn nút Kiểm tra ({sel})!")
                    return True
            except Exception:
                continue

        await asyncio.sleep(0.35)

    print("⚠️ Không tìm thấy nút Kiểm tra trên trang.")
    return False


async def check_answer_result(page: Page) -> str:
    """
    Kiểm tra kết quả sau khi đã nhấn nút Kiểm tra.
    Trả về: 'correct', 'incorrect', hoặc 'checked'
    """
    await asyncio.sleep(1.2)
    try:
        correct = await page.locator(
            '[class*="correct"], [data-testid*="correct"], [data-test-id*="correct"], [aria-label*="Correct" i], [aria-label*="Chính xác" i], svg[aria-label*="Correct" i]'
        ).count()
        incorrect = await page.locator(
            '[class*="incorrect"], [data-testid*="incorrect"], [data-test-id*="incorrect"], [aria-label*="Incorrect" i], [aria-label*="Không chính xác" i], [aria-label*="Not quite" i]'
        ).count()
        if correct > 0:
            return "correct"
        elif incorrect > 0:
            return "incorrect"
    except Exception:
        pass
    return "checked"


async def click_next_question_button(page: Page) -> bool:
    """
    Tự động nhấn nút chuyển câu (Next question / Tiếp theo) nếu xuất hiện.
    """
    await asyncio.sleep(0.8)
    next_selectors = [
        'button[data-testid="next-question-button"]',
        'button[data-testid*="next"]',
        'button[data-test-id="next-question-button"]',
        'button[data-test-id*="next"]',
        'button:has-text("Next question")',
        'button:has-text("Next")',
        'button:has-text("Tiếp theo")',
        'button:has-text("Câu tiếp theo")',
        'button:has-text("Keep going")',
        'button:has-text("Tiếp tục")',
        'button:has-text("Show summary")',
        'button:has-text("Xem tóm tắt")',
        'button:has-text("Done")',
        'button:has-text("Hoàn thành")',
        'button:has-text("Bài tập tiếp theo")',
        'a:has-text("Bài tập tiếp theo")',
        'button:has-text("Next assignment")',
        'a:has-text("Next assignment")',
        'button:has-text("Onward")',
        'a:has-text("Onward")',
        'button[aria-label*="Next" i]',
        'button[aria-label*="Tiếp theo" i]',
        '[data-testid*="bottom-nav"] button',
    ]

    for sel in next_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                if not await btn.is_enabled():
                    await asyncio.sleep(0.2)
                await btn.scroll_into_view_if_needed()
                try:
                    await btn.click(timeout=2500, force=True)
                except Exception:
                    await btn.evaluate("el => el.click()")
                print(f"🖱️  ✅ Đã tự động nhấn nút chuyển câu ({sel})!")
                return True
        except Exception:
            continue
    return False


async def click_check_or_next(page: Page) -> str:
    """
    Nhấn nút 'Kiểm tra' hoặc 'Tiếp theo'.
    Trả về: 'correct', 'incorrect', 'next', 'done'
    """
    checked = await click_check_answer_button(page)
    if checked:
        return await check_answer_result(page)

    # Nếu không có nút Check, thử nút Next hoặc Done
    if await click_next_question_button(page):
        return "next"

    return "no_button"


# ══════════════════════════════════════════════════
#  HOÀN THÀNH BÀI TẬP & BÀI TẬP TIẾP THEO
# ══════════════════════════════════════════════════

async def click_next_assignment_button(page: Page) -> bool:
    """
    Tự động tìm và bấm nút 'Bài tập tiếp theo' (Next assignment / Onward) khi hoàn thành bài tập.
    """
    print("👉 Đang tìm và bấm nút 'Bài tập tiếp theo'...")
    await asyncio.sleep(0.5)

    selectors = [
        'button:has-text("Bài tập tiếp theo")',
        'a:has-text("Bài tập tiếp theo")',
        '[role="button"]:has-text("Bài tập tiếp theo")',
        'button:has-text("Next assignment")',
        'a:has-text("Next assignment")',
        '[role="button"]:has-text("Next assignment")',
        'button:has-text("Onward")',
        'a:has-text("Onward")',
        'button:has-text("Keep going")',
        'a:has-text("Keep going")',
        'button:has-text("Bài tiếp theo")',
        'a:has-text("Bài tiếp theo")',
        'button:has-text("Luyện tập tiếp")',
        'a:has-text("Luyện tập tiếp")',
        '[data-testid*="next-assignment"]',
        '[data-test-id*="next-assignment"]',
        'button[data-testid*="next"]',
        'a[data-testid*="next"]',
    ]

    for attempt in range(5):
        for sel in selectors:
            try:
                loc = page.locator(sel)
                count = await loc.count()
                for i in range(count):
                    btn = loc.nth(i)
                    if await btn.is_visible():
                        print(f"🎯 Tìm thấy nút 'Bài tập tiếp theo' ({sel})! Đang nhấn...")
                        await btn.scroll_into_view_if_needed()
                        await asyncio.sleep(0.2)
                        try:
                            await btn.click(timeout=3000, force=True)
                        except Exception:
                            await btn.evaluate("el => el.click()")
                        print("🖱️ ✅ Đã nhấn nút 'Bài tập tiếp theo' thành công!")
                        return True
            except Exception:
                continue

        # Fallback Javascript quét tất cả elements tìm text 'Bài tập tiếp theo'
        try:
            clicked = await page.evaluate("""() => {
                const targets = ['bài tập tiếp theo', 'next assignment', 'onward', 'keep going', 'bài tiếp theo', 'luyện tập tiếp'];
                const elements = Array.from(document.querySelectorAll('button, a, div[role="button"], span, p, div'));
                for (const el of elements) {
                    const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (targets.some(t => text === t || text.startsWith(t))) {
                        const clickable = el.closest('button, a, div[role="button"]') || el;
                        clickable.scrollIntoView({ behavior: 'instant', block: 'center' });
                        clickable.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                print("🖱️ ✅ Đã nhấn nút 'Bài tập tiếp theo' qua JS fallback!")
                return True
        except Exception:
            pass

        await asyncio.sleep(0.5)

    print("⚠️ Không tìm thấy nút 'Bài tập tiếp theo'.")
    return False


async def check_exercise_completion(page: Page) -> bool:
    """Kiểm tra xem toàn bộ bài tập đã hoàn thành chưa (tóm tắt, thành thạo, hoặc có nút Bài tập tiếp theo)."""
    try:
        # 1. Bấm nút xem tóm tắt / tổng kết nếu có
        for sel in [
            'button:has-text("Show summary")',
            'button:has-text("Xem tóm tắt")',
            'button:has-text("View summary")',
            'button[data-testid*="summary"]'
        ]:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(force=True)
                await asyncio.sleep(1.2)
                return True

        # 2. Kiểm tra xem đã hiển thị nút "Bài tập tiếp theo" hoặc các nút/dấu hiệu hoàn thành
        completion_selectors = [
            'button:has-text("Bài tập tiếp theo")',
            'a:has-text("Bài tập tiếp theo")',
            '[role="button"]:has-text("Bài tập tiếp theo")',
            'button:has-text("Next assignment")',
            'a:has-text("Next assignment")',
            'button:has-text("Onward")',
            'a:has-text("Onward")',
            'button:has-text("Bắn pháo giấy")',
            'text="Thành thạo"',
            '[data-testid="exercise-summary"]',
            '[data-testid="summary-view"]',
            '[class*="exercise-summary"]',
            'h1:has-text("Summary")',
            'h1:has-text("Tóm tắt")',
            'h2:has-text("Summary")',
            'h2:has-text("Tóm tắt")',
            'div:has-text("You\'re all done")',
            'div:has-text("Hoàn thành bài tập")',
        ]
        for sel in completion_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                return True
    except Exception:
        pass
    return False


# ══════════════════════════════════════════════════
#  MAIN SOLVER LOOP
# ══════════════════════════════════════════════════

async def solve_exercise(page: Page, retry_count: int = 0, max_retries: int = 1) -> None:
    """Giải một bài tập đơn lẻ và tự động nhấn nút Kiểm tra sau khi hoàn thành."""
    # Nếu đang ở màn hình hoàn thành bài tập, bấm Bài tập tiếp theo ngay
    if await check_exercise_completion(page):
        print("🎉 Phát hiện màn hình hoàn thành bài tập! Đang nhấn 'Bài tập tiếp theo'...")
        await click_next_assignment_button(page)
        return

    await asyncio.sleep(DELAY_BETWEEN)
    ex_type = await detect_exercise_type(page)
    print(f"🔍 Phát hiện loại bài: {ex_type}")

    handlers = {
        "multiple_choice": handle_multiple_choice,
        "input":           handle_input_answer,
        "expression":      handle_expression,
        "matcher":         handle_matcher_answer,
    }

    handler = handlers.get(ex_type, handle_unknown)
    success = await handler(page)

    if success:
        print("🎉 Đã hoàn thành điền đáp án! Đang tự động nhấn nút Kiểm tra...")
        # Đợi 0.5s để React đồng bộ trạng thái
        await asyncio.sleep(0.5)

        # 1. TỰ ĐỘNG NHẤN NÚT KIỂM TRA
        result = await click_check_or_next(page)
        print(f"📊 Kết quả kiểm tra: {result}")

        # 2. Xử lý sau khi kiểm tra
        if result == "incorrect" and retry_count < max_retries:
            print("❌ Chưa chính xác! Đang thử lại 1 lần nữa...")
            await asyncio.sleep(1.0)
            await solve_exercise(page, retry_count=retry_count + 1, max_retries=max_retries)
        elif result in ("correct", "checked"):
            print("✨ Câu trả lời chính xác!")
            # 3. Tự động nhấn nút chuyển câu tiếp theo
            await click_next_question_button(page)
            # 4. Kiểm tra xem đã kết thúc toàn bộ bài tập chưa (nếu đây là câu cuối cùng)
            await asyncio.sleep(1.2)
            if await check_exercise_completion(page):
                print("🎉 Đã hoàn thành toàn bộ bài tập! Đang nhấn 'Bài tập tiếp theo'...")
                await click_next_assignment_button(page)


# ══════════════════════════════════════════════════
#  TRẠNG THÁI & ĐIỀU KHIỂN GIAO DIỆN
# ══════════════════════════════════════════════════

is_solving = False
auto_solve_enabled = False


async def update_ui_controls(page: Page, auto_running: bool = False, status_text: str | None = None) -> None:
    """Cập nhật giao diện của cả 2 nút bấm trên trang web."""
    try:
        await page.evaluate("""([running, text]) => {
            const btnSingle = document.getElementById('ai-btn-single');
            const btnAuto = document.getElementById('ai-btn-auto');

            if (btnAuto) {
                if (running) {
                    btnAuto.innerHTML = '⏹️ Dừng tự động làm bài';
                    btnAuto.style.background = '#dc2626';
                    btnAuto.style.boxShadow = '0 6px 18px rgba(220,38,38,0.5)';
                } else {
                    btnAuto.innerHTML = '🚀 Tự động làm bài (Auto)';
                    btnAuto.style.background = '#059669';
                    btnAuto.style.boxShadow = '0 6px 18px rgba(5,150,105,0.45)';
                }
            }

            if (btnSingle && text) {
                btnSingle.innerHTML = text;
                if (running) {
                    btnSingle.style.background = '#ea580c';
                } else {
                    btnSingle.style.background = '#1865f2';
                }
            }
        }""", [auto_running, status_text])
    except Exception:
        pass


async def auto_solve_loop(context, initial_page: Page) -> None:
    """
    VÒNG LẶP TỰ ĐỘNG LÀM BÀI TOÀN DIỆN:
    Tự giải -> điền đáp án -> bấm Kiểm tra -> bấm Tiếp theo -> giải câu tiếp theo
    -> khi hoàn thành bài thì tự động bấm 'Bài tập tiếp theo' -> tiếp tục làm bài mới!
    """
    global is_solving, auto_solve_enabled
    question_count = 0
    exercise_count = 1

    print("\n" + "═" * 60)
    print("🚀 CHẾ ĐỘ TỰ ĐỘNG LÀM BÀI (AUTO) ĐÃ BẬT!")
    print("👉 AI sẽ tự giải, tự điền, tự kiểm tra, chuyển câu và tự chuyển bài tập tiếp theo.")
    print("👉 Bạn có thể bấm [⏹️ Dừng tự động] hoặc phím [a] để dừng bất cứ lúc nào.")
    print("═" * 60 + "\n")

    while auto_solve_enabled:
        current_page = context.pages[-1] if context.pages else initial_page

        # 1. Kiểm tra bài tập đã kết thúc chưa
        if await check_exercise_completion(current_page):
            print(f"\n🎉 BÀI TẬP #{exercise_count} ĐÃ HOÀN THÀNH XUẤT SẮC!")
            await update_ui_controls(current_page, auto_running=True, status_text="🎉 Hoàn thành bài! Đang chuyển bài tiếp...")
            await asyncio.sleep(1.0)
            clicked = await click_next_assignment_button(current_page)
            if clicked:
                exercise_count += 1
                question_count = 0
                print(f"🚀 Đang chuyển sang bài tập tiếp theo #{exercise_count}...")
                await update_ui_controls(current_page, auto_running=True, status_text=f"🚀 Đang tải bài #{exercise_count}...")
                await asyncio.sleep(4.0)
                continue
            else:
                print("🏁 Đã hoàn thành hết bài tập hoặc không tìm thấy nút 'Bài tập tiếp theo'.")
                auto_solve_enabled = False
                await update_ui_controls(current_page, auto_running=False, status_text="🏁 Đã hoàn thành toàn bộ bài tập!")
                break

        question_count += 1
        print(f"\n────────────────────────────────────────────")
        print(f"🤖 [Tự động - Bài {exercise_count}] Đang giải câu hỏi {question_count}...")
        print(f"────────────────────────────────────────────")

        await update_ui_controls(
            current_page,
            auto_running=True,
            status_text=f"🤖 Bài {exercise_count} - Câu {question_count}..."
        )

        is_solving = True
        try:
            await solve_exercise(current_page)
            print(f"✅ [Tự động] Đã hoàn thành câu {question_count}!")
        except Exception as e:
            print(f"⚠️ Lỗi khi giải câu {question_count}: {e}")
        finally:
            is_solving = False

        if not auto_solve_enabled:
            print("⏹️ Đã dừng chế độ tự động làm bài theo yêu cầu.")
            break

        # Kiểm tra lại xem sau câu vừa rồi bài tập đã kết thúc chưa
        if await check_exercise_completion(current_page):
            print(f"\n🎉 BÀI TẬP #{exercise_count} ĐÃ HOÀN THÀNH XUẤT SẮC!")
            await update_ui_controls(current_page, auto_running=True, status_text="🎉 Hoàn thành bài! Đang chuyển bài tiếp...")
            await asyncio.sleep(1.0)
            clicked = await click_next_assignment_button(current_page)
            if clicked:
                exercise_count += 1
                question_count = 0
                print(f"🚀 Đang chuyển sang bài tập tiếp theo #{exercise_count}...")
                await update_ui_controls(current_page, auto_running=True, status_text=f"🚀 Đang tải bài #{exercise_count}...")
                await asyncio.sleep(4.0)
                continue
            else:
                print("🏁 Đã hoàn thành hết bài tập hoặc không tìm thấy nút 'Bài tập tiếp theo'.")
                auto_solve_enabled = False
                await update_ui_controls(current_page, auto_running=False, status_text="🏁 Đã hoàn thành toàn bộ bài tập!")
                break

        # Nghỉ giữa các câu để giao diện tải câu mới
        wait_time = max(AI_PAUSE_SECONDS, 2.5)
        print(f"⏳ Nghỉ {wait_time:.1f}s chuẩn bị câu tiếp theo...")
        await update_ui_controls(
            current_page,
            auto_running=True,
            status_text=f"⏳ Nghỉ {wait_time:.1f}s trước câu {question_count+1}..."
        )

        # Chờ ngắt quãng để phản hồi nút Dừng ngay lập tức
        for _ in range(int(wait_time * 5)):
            if not auto_solve_enabled:
                break
            await asyncio.sleep(0.2)

    await update_ui_controls(
        context.pages[-1] if context.pages else initial_page,
        auto_running=False,
        status_text="⚡ Giải câu này <b>[/]</b>"
    )


async def main_supervisor_loop(context, initial_page: Page, single_trigger_event: asyncio.Event, auto_toggle_event: asyncio.Event) -> None:
    """
    Điều phối luồng hoạt động chính:
    Hỗ trợ song song cả 2 chế độ:
    1. Chế độ giải từng câu theo yêu cầu (phím [/] hoặc nút [⚡ Giải câu này])
    2. Chế độ tự động làm toàn bộ bài tập (nút [🚀 Tự động làm bài] hoặc phím [a])
    """
    global is_solving, auto_solve_enabled

    print("\n" + "═" * 60)
    print("🎯 HỆ THỐNG ĐIỀU KHIỂN AI ĐÃ SẴN SÀNG!")
    print("👉 [Cách 1 - Tự động]: Bấm nút xanh lá [🚀 Tự động làm bài] hoặc phím [a]")
    print("   -> AI sẽ tự giải, tự điền, tự kiểm tra từ đầu đến cuối bài!")
    print("👉 [Cách 2 - Từng câu]: Nhấn phím [/] hoặc nút xanh dương [⚡ Giải câu này]")
    print("   -> AI chỉ giải đúng 1 câu hiện tại và dừng.")
    print("═" * 60 + "\n")

    question_idx = 0

    while True:
        current_page = context.pages[-1] if context.pages else initial_page

        # Nếu chế độ tự động đang bật -> chuyển vào vòng lặp tự động
        if auto_solve_enabled:
            await auto_solve_loop(context, current_page)
            auto_solve_enabled = False
            continue

        await update_ui_controls(current_page, auto_running=False, status_text="⚡ Giải câu này <b>[/]</b>")

        # Chờ người dùng kích hoạt 1 trong 2 nút/phím tắt
        task_single = asyncio.create_task(single_trigger_event.wait())
        task_auto = asyncio.create_task(auto_toggle_event.wait())

        done, pending = await asyncio.wait([task_single, task_auto], return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()

        if auto_toggle_event.is_set():
            auto_toggle_event.clear()
            if auto_solve_enabled:
                continue

        if single_trigger_event.is_set():
            single_trigger_event.clear()
            current_page = context.pages[-1] if context.pages else initial_page

            # Kiểm tra nếu đang ở màn hình hoàn thành bài tập -> bấm Bài tập tiếp theo
            if await check_exercise_completion(current_page):
                print("\n🎉 Phát hiện màn hình hoàn thành bài tập! Đang nhấn 'Bài tập tiếp theo'...")
                await update_ui_controls(current_page, auto_running=False, status_text="🎉 Đang chuyển bài tập tiếp theo...")
                clicked = await click_next_assignment_button(current_page)
                if clicked:
                    await asyncio.sleep(3.0)
                    await update_ui_controls(current_page, auto_running=False, status_text="✅ Đã chuyển bài mới! Nhấn <b>[/]</b> để giải")
                else:
                    await update_ui_controls(current_page, auto_running=False, status_text="⚡ Giải câu này <b>[/]</b>")
                continue

            question_idx += 1
            print(f"\n🚀 [Phím / kích hoạt] AI đang giải câu {question_idx}...")
            await update_ui_controls(current_page, auto_running=False, status_text="🤖 AI đang phân tích & giải...")

            is_solving = True
            try:
                await solve_exercise(current_page)
                if await check_exercise_completion(current_page):
                    await update_ui_controls(current_page, auto_running=False, status_text="🎉 Hoàn thành bài! Nhấn <b>[/]</b> để sang bài tiếp")
                else:
                    await update_ui_controls(current_page, auto_running=False, status_text="✅ Đã giải xong! Nhấn <b>[/]</b> cho câu tiếp")
            except Exception as e:
                print(f"❌ Lỗi khi giải câu: {e}")
                await update_ui_controls(current_page, auto_running=False, status_text="⚠️ Có lỗi! Nhấn <b>[/]</b> để thử lại")
            finally:
                is_solving = False


# ══════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════

async def main():
    global is_solving, auto_solve_enabled

    print("╔══════════════════════════════════╗")
    print("║  Khan Academy AI Auto-Solver     ║")
    print("╚══════════════════════════════════╝")

    async with async_playwright() as p:
        user_data_dir = Path("user_data")
        user_data_dir.mkdir(exist_ok=True)

        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=HEADLESS,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="vi-VN",
            no_viewport=True,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        single_trigger_event = asyncio.Event()
        auto_toggle_event = asyncio.Event()

        # Expose các hàm trigger từ trình duyệt ra Python
        async def on_browser_single_trigger(source):
            global is_solving, auto_solve_enabled
            if is_solving:
                print("⚠️ AI đang xử lý câu hiện tại, vui lòng đợi xong...")
                return
            if auto_solve_enabled:
                print("ℹ️ Chế độ tự động đang chạy. Bấm [Dừng tự động] nếu muốn điều khiển từng câu.")
                return
            print("\n⚡ [Trình duyệt] Nhận phím [/] -> Bắt đầu giải 1 câu!")
            single_trigger_event.set()

        async def on_browser_auto_toggle(source):
            global auto_solve_enabled
            auto_solve_enabled = not auto_solve_enabled
            if auto_solve_enabled:
                print("\n🚀 [Trình duyệt] Đã BẬT chế độ TỰ ĐỘNG LÀM BÀI (Auto-Solve)!")
            else:
                print("\n⏹️ [Trình duyệt] Đã TẮT chế độ tự động làm bài.")
            auto_toggle_event.set()

        await context.expose_binding("triggerAiSolve", on_browser_single_trigger)
        await context.expose_binding("toggleAutoSolve", on_browser_auto_toggle)

        # Inject giao diện 2 nút bấm và phím tắt trên tất cả các trang
        await context.add_init_script("""
            window.addEventListener('keydown', (e) => {
                const active = document.activeElement;
                const isTyping = active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable || (active.classList && active.classList.contains('mq-editable-field')));

                if (e.key === '/' && !isTyping) {
                    if (window.triggerAiSolve) {
                        e.preventDefault();
                        window.triggerAiSolve();
                    }
                } else if ((e.key === 'a' || e.key === 'A') && !isTyping && !e.ctrlKey && !e.metaKey) {
                    if (window.toggleAutoSolve) {
                        e.preventDefault();
                        window.toggleAutoSolve();
                    }
                }
            }, true);

            function setupPanel() {
                if (document.getElementById('ai-solver-panel')) return;

                const panel = document.createElement('div');
                panel.id = 'ai-solver-panel';
                panel.style.cssText = 'position:fixed;bottom:30px;left:30px;display:flex;flex-direction:column;gap:8px;z-index:9999999;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;user-select:none;background:rgba(15,23,42,0.88);backdrop-filter:blur(8px);padding:10px 12px;border-radius:18px;border:1px solid rgba(255,255,255,0.18);box-shadow:0 12px 32px rgba(0,0,0,0.55);';

                // Thanh kéo thả di chuyển vị trí bảng điều khiển
                const header = document.createElement('div');
                header.style.cssText = 'font-size:11px;font-weight:700;color:#94a3b8;cursor:move;padding:2px 4px 4px 4px;display:flex;justify-content:space-between;align-items:center;letter-spacing:0.5px;';
                header.innerHTML = '<span>⋮⋮ AI Auto-Solver</span><span style="font-size:10px;opacity:0.75;">(Kéo để di chuyển)</span>';

                // Hỗ trợ kéo thả (drag & drop) bằng chuột
                let isDragging = false, startX = 0, startY = 0, initialLeft = 0, initialTop = 0;
                header.addEventListener('mousedown', (e) => {
                    isDragging = true;
                    startX = e.clientX;
                    startY = e.clientY;
                    const rect = panel.getBoundingClientRect();
                    initialLeft = rect.left;
                    initialTop = rect.top;
                    panel.style.bottom = 'auto';
                    panel.style.right = 'auto';
                    panel.style.left = initialLeft + 'px';
                    panel.style.top = initialTop + 'px';
                });

                window.addEventListener('mousemove', (e) => {
                    if (!isDragging) return;
                    const dx = e.clientX - startX;
                    const dy = e.clientY - startY;
                    const maxLeft = window.innerWidth - panel.offsetWidth - 10;
                    const maxTop = window.innerHeight - panel.offsetHeight - 10;
                    panel.style.left = Math.max(10, Math.min(maxLeft, initialLeft + dx)) + 'px';
                    panel.style.top = Math.max(10, Math.min(maxTop, initialTop + dy)) + 'px';
                });

                window.addEventListener('mouseup', () => {
                    isDragging = false;
                });

                // Nút 1: Giải 1 câu
                const btnSingle = document.createElement('button');
                btnSingle.id = 'ai-btn-single';
                btnSingle.style.cssText = 'background:#1865f2;color:white;border:none;padding:9px 16px;border-radius:20px;font-size:13px;font-weight:600;box-shadow:0 3px 10px rgba(0,0,0,0.25);cursor:pointer;transition:all 0.2s cubic-bezier(0.25,0.8,0.25,1);display:flex;align-items:center;justify-content:center;gap:6px;';
                btnSingle.innerHTML = '⚡ Giải câu này <b>[/]</b>';
                btnSingle.onmouseenter = () => { btnSingle.style.transform = 'scale(1.03)'; };
                btnSingle.onmouseleave = () => { btnSingle.style.transform = 'scale(1.0)'; };
                btnSingle.onclick = () => {
                    if (window.triggerAiSolve) window.triggerAiSolve();
                };

                // Nút 2: Tự động làm toàn bài
                const btnAuto = document.createElement('button');
                btnAuto.id = 'ai-btn-auto';
                btnAuto.style.cssText = 'background:#059669;color:white;border:none;padding:11px 20px;border-radius:20px;font-size:14px;font-weight:700;box-shadow:0 4px 14px rgba(5,150,105,0.4);cursor:pointer;transition:all 0.2s cubic-bezier(0.25,0.8,0.25,1);display:flex;align-items:center;justify-content:center;gap:8px;';
                btnAuto.innerHTML = '🚀 Tự động làm bài (Auto)';
                btnAuto.onmouseenter = () => { btnAuto.style.transform = 'scale(1.03)'; };
                btnAuto.onmouseleave = () => { btnAuto.style.transform = 'scale(1.0)'; };
                btnAuto.onclick = () => {
                    if (window.toggleAutoSolve) window.toggleAutoSolve();
                };

                panel.appendChild(header);
                panel.appendChild(btnSingle);
                panel.appendChild(btnAuto);
                document.body.appendChild(panel);
            }

            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', setupPanel);
            } else {
                setupPanel();
            }
            setInterval(setupPanel, 2000);
        """)

        # Lắng nghe phím từ terminal (PowerShell / CMD)
        async def terminal_key_listener():
            global is_solving, auto_solve_enabled
            while True:
                try:
                    if msvcrt and msvcrt.kbhit():
                        ch = msvcrt.getwch().lower()
                        if ch == '/':
                            if is_solving:
                                print("⚠️ AI đang xử lý câu hiện tại, vui lòng đợi xong...")
                            elif not auto_solve_enabled:
                                print("\n⚡ [Terminal] Đã nhấn phím [/] -> Giải 1 câu!")
                                single_trigger_event.set()
                        elif ch == 'a':
                            auto_solve_enabled = not auto_solve_enabled
                            if auto_solve_enabled:
                                print("\n🚀 [Terminal] Đã BẬT chế độ TỰ ĐỘNG LÀM BÀI (Auto-Solve)!")
                            else:
                                print("\n⏹️ [Terminal] Đã TẮT chế độ tự động làm bài.")
                            auto_toggle_event.set()
                except Exception:
                    pass
                await asyncio.sleep(0.08)

        listener_task = asyncio.create_task(terminal_key_listener())
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            already_logged_in = await login(page)

            is_default_url = KA_TARGET_URL.rstrip("/") in [
                "https://www.khanacademy.org",
                "https://khanacademy.org",
                "https://classroom.khanacademy.org"
            ]

            if already_logged_in and is_default_url:
                print(f"\n📚 Bạn đang ở trang lớp học: {CLASSROOM_URL}")
                print("👉 Hãy mở bài tập cần làm. Bất cứ khi nào muốn AI làm tự động, hãy bấm nút xanh lá [🚀 Tự động làm bài]!")
            else:
                print(f"\n🌐 Đang mở: {KA_TARGET_URL}")
                await page.goto(KA_TARGET_URL, wait_until="domcontentloaded", timeout=60000)

            # Chạy hệ thống điều phối hỗ trợ cả giải 1 câu và tự động làm toàn bộ
            await main_supervisor_loop(context, page, single_trigger_event, auto_toggle_event)

        except KeyboardInterrupt:
            print("\n⏹️ Dừng bởi người dùng.")
        except Exception as e:
            print(f"\n❌ Lỗi: {e}")
            import traceback
            traceback.print_exc()
        finally:
            listener_task.cancel()
            print("\n🔒 Đóng trình duyệt...")
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())
