from __future__ import annotations

import asyncio
import json

from core.config import get_settings
from services.executors.visit_real_runner import _PtsBrowserSession


URL = "https://pts.chaitin.net/project/order/69143a964169b1cf477144c6"

CLICK_ASSIGN_OWNER_JS = """
(() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const btn = Array.from(document.querySelectorAll('button')).find((b) => norm(b.innerText).includes('指定工单负责人'));
  if (!btn) return {clicked:false, reason:'button_not_found'};
  btn.click();
  return {clicked:true};
})()
"""

DUMP_PAGE_STATE_JS = """
(() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  return {
    buttons: Array.from(document.querySelectorAll('button'))
      .map((b, i) => ({i, text: norm(b.innerText), disabled: !!b.disabled}))
      .filter((x) => x.text)
      .slice(0, 120),
    inputs: Array.from(document.querySelectorAll('input, textarea'))
      .map((el, i) => ({
        i,
        tag: el.tagName,
        type: el.type || '',
        placeholder: el.placeholder || '',
        value: (el.value || '').slice(0, 120),
      }))
      .slice(0, 80),
    dialogs: Array.from(
      document.querySelectorAll('[role="dialog"], .ant-modal, .ant-drawer, .el-dialog, [class*="modal"], [class*="dialog"]')
    ).map((el, i) => ({i, text: norm(el.innerText).slice(0, 1000)})).slice(0, 20),
    shuleiNodes: Array.from(document.querySelectorAll('*'))
      .map((el, i) => ({
        i,
        tag: el.tagName,
        cls: el.className || '',
        text: norm(el.innerText || ''),
        role: el.getAttribute && el.getAttribute('role'),
      }))
      .filter((item) => item.text === '舒磊' || item.text === '刘超' || item.text === '田疆')
      .slice(0, 40),
    clickableText: Array.from(document.querySelectorAll('button, a, [role=\"button\"], label, span, div'))
      .map((el, i) => ({
        i,
        tag: el.tagName,
        cls: el.className || '',
        text: norm(el.innerText || '').slice(0, 80),
      }))
      .filter((item) => item.text && (
        item.text.includes('指定工单负责人') ||
        item.text.includes('开始处理工单') ||
        item.text.includes('完成工单处理') ||
        item.text.includes('审核工单') ||
        item.text === '舒磊' ||
        item.text === '刘超' ||
        item.text === '田疆' ||
        item.text === '保存' ||
        item.text === '取消'
      ))
      .slice(0, 120),
    body: norm(document.body ? document.body.innerText : '').slice(0, 4000),
  };
})()
"""

ASSIGN_OWNER_JS = """
(() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const dialogs = Array.from(document.querySelectorAll('.el-dialog, [role="dialog"], .ant-modal')).filter((el) => el && el.offsetParent !== null);
  const dialog = dialogs[0] || document;
  const candidate = Array.from(dialog.querySelectorAll('*')).find((el) => norm(el.innerText) === '舒磊');
  if (!candidate) return {ok:false, reason:'owner_candidate_not_found'};
  candidate.click();
  const saveBtn = Array.from(dialog.querySelectorAll('button')).find((btn) => norm(btn.innerText) === '保存');
  if (!saveBtn) return {ok:false, reason:'save_not_found'};
  saveBtn.click();
  return {ok:true};
})()
"""


async def main() -> None:
    settings = get_settings()
    async with _PtsBrowserSession(settings) as session:
        print("OPEN", await session.open_project(URL))
        await asyncio.sleep(1)
        print("CLICK", await session.execute_js(CLICK_ASSIGN_OWNER_JS))
        await asyncio.sleep(2)
        print("ASSIGN", await session.execute_js(ASSIGN_OWNER_JS))
        await asyncio.sleep(3)
        print(json.dumps(await session.execute_js(DUMP_PAGE_STATE_JS), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
