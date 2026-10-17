import sys, time, json
from playwright.sync_api import sync_playwright
URL = "http://127.0.0.1:5173/"
def launch(p):
    for ch in ("chrome", "msedge"):
        try:
            return p.chromium.launch(channel=ch, headless=True)
        except Exception as e:
            last = e
    return p.chromium.launch(headless=True)
def main(actions):
    with sync_playwright() as p:
        b = launch(p)
        ctx = b.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
        page = ctx.new_page()
        logs = []
        page.on("console", lambda m: logs.append(f"[{m.type}] {m.text}") if m.type in ("error","warning") else None)
        page.on("pageerror", lambda e: logs.append(f"[pageerror] {e} {getattr(e, 'stack', '')}"))
        page.goto(URL, wait_until="networkidle")
        for act in actions:
            kind = act[0]
            if kind == "wait": time.sleep(act[1])
            elif kind == "click": page.click(act[1]); time.sleep(0.4)
            elif kind == "text": page.get_by_text(act[1], exact=act[2] if len(act)>2 else False).first.click(); time.sleep(0.4)
            elif kind == "shot": page.screenshot(path=f"data/shots/{act[1]}.png")
            elif kind == "key": page.keyboard.press(act[1]); time.sleep(0.3)
            elif kind == "eval": print(page.evaluate(act[1]))
            elif kind == "mouse": page.mouse.click(act[1], act[2]); time.sleep(0.4)
            elif kind == "wheel": page.mouse.move(act[1], act[2]); page.mouse.wheel(0, act[3]); time.sleep(0.3)
            elif kind == "viewport": page.set_viewport_size({"width": act[1], "height": act[2]}); time.sleep(0.3)
        for l in logs[:30]: print(l)
        b.close()
if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
