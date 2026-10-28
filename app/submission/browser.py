"""Conservative Playwright adapter for hosted Greenhouse and Lever forms."""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from app.config import get_settings
from app.domain.models import Profile
from app.submission.models import BrowserSubmissionResult, FormSnapshot, SubmissionPlan
from app.submission.planning import SubmissionBlocked, _host_allowed, adapter_for_url, build_submission_plan, form_fingerprint, resolve_plan_value


class HostedFormBrowser:
    def __init__(self, *, headless: bool = True, timeout_ms: int = 15000):
        self.headless = headless
        self.timeout_ms = timeout_ms

    async def inspect(self, job_url: str) -> FormSnapshot:
        _, page_hosts, action_hosts = adapter_for_url(job_url)
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            try:
                context = await browser.new_context(service_workers="block")
                page = await context.new_page()
                await self._install_request_guard(page, page_hosts | action_hosts)
                try:
                    await page.goto(job_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                except Exception as exc:
                    raise SubmissionBlocked("SAFE_NAVIGATION_BLOCKED", "The application page could not be loaded safely.") from exc
                await page.locator("body").wait_for()
                result = await page.evaluate("""() => {
              const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
              const forms = [...document.forms];
              const form = forms.length === 1 ? forms[0] : null;
              const fields = form ? [...form.querySelectorAll('input,textarea,select')].map((e, i) => {
                const label = e.labels?.[0]?.innerText || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '';
                return { key: e.id ? `id:${e.id}` : e.name ? `name:${e.name}` : `index:${i}`, label: label.trim().slice(0,300),
                  control_type: e.tagName === 'TEXTAREA' ? 'textarea' : e.tagName === 'SELECT' ? 'select' : (e.type || 'text'),
                  required: !!e.required || e.getAttribute('aria-required') === 'true', visible: visible(e),
                  options: e.tagName === 'SELECT' ? [...e.options].map(o => o.text.trim()).slice(0,100) : [] };
              }) : [];
              return { current_url: location.href, page_text: document.body?.innerText?.slice(0,20000) || '', form_count: forms.length,
                submit_button_count: form ? form.querySelectorAll('button[type=submit],input[type=submit],button:not([type])').length : 0,
                form_action: form?.action || '', form_method: form?.method || '', fields,
                frame_urls: [...document.querySelectorAll('iframe')].map(f => f.src).slice(0,50) };
                }""")
            finally:
                await browser.close()
        snapshot = FormSnapshot.model_validate(result)
        if not _host_allowed(snapshot.current_url, page_hosts):
            raise SubmissionBlocked("OFFSITE_REDIRECT", "The application page redirected outside the supported ATS host.")
        return snapshot

    async def submit(self, plan: SubmissionPlan, profile: Profile, artifact_paths: dict[str, str]) -> BrowserSubmissionResult:
        _, page_hosts, action_hosts = adapter_for_url(plan.job_url)
        if plan.adapter not in {"greenhouse", "lever"} or not _host_allowed(plan.form_action, action_hosts):
            raise SubmissionBlocked("UNSUPPORTED_FORM_ACTION", "The saved form destination is no longer supported.")
        from playwright.async_api import async_playwright

        clicked = False
        screenshot = None
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            try:
                context = await browser.new_context(service_workers="block")
                page = await context.new_page()
                await self._install_request_guard(page, page_hosts | action_hosts)
                await page.goto(plan.job_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                await page.locator("body").wait_for()
                snap = await self._snapshot_current_page(page)
                if not _host_allowed(snap.current_url, page_hosts) or form_fingerprint(snap) != plan.form_fingerprint:
                    raise SubmissionBlocked("FORM_CHANGED", "The hosted application form changed after review. Prepare it again.")
                # Re-run the same conservative field, challenge, and destination checks.
                refreshed = build_submission_plan(
                    snap, profile, application_id=UUID(plan.application_id),
                    approval_id=UUID(plan.approval_id), document_version=plan.document_version,
                    verification_id=UUID(plan.verification_id), answers_hash=plan.answers_hash,
                    profile_hash=plan.profile_hash, job_url=plan.job_url, artifact_paths=artifact_paths,
                    artifact_hashes={item.document_key: item.source_hash for item in plan.fields if item.document_key},
                )
                if refreshed.plan_hash != plan.plan_hash:
                    raise SubmissionBlocked("FORM_CHANGED", "The reviewed form fields or documents changed. Prepare the form again.")
                for item in plan.fields:
                    value = resolve_plan_value(profile, item, artifact_paths)
                    if not value:
                        raise SubmissionBlocked("SOURCE_CHANGED", "A reviewed profile value or document changed. Prepare the form again.")
                    locator = self._locator(page, item.field_key)
                    if item.document_key:
                        import hashlib
                        artifact_root = Path(get_settings().artifacts_path).expanduser().resolve()
                        artifact_file = (artifact_root / value).resolve()
                        if not artifact_file.is_relative_to(artifact_root) or not artifact_file.is_file():
                            raise SubmissionBlocked("DOCUMENT_MISSING", "An approved document is unavailable in private storage.")
                        if hashlib.sha256(artifact_file.read_bytes()).hexdigest() != item.source_hash:
                            raise SubmissionBlocked("DOCUMENT_CHANGED", "An approved attachment changed after review.")
                        await locator.set_input_files(str(artifact_file))
                    else:
                        import hashlib
                        if hashlib.sha256(value.encode()).hexdigest() != item.source_hash:
                            raise SubmissionBlocked("SOURCE_CHANGED", "A reviewed profile value or document changed. Prepare the form again.")
                        await locator.fill(value)
                validity = await page.locator("form").evaluate("f => f.checkValidity()")
                if not validity:
                    raise SubmissionBlocked("FORM_VALIDATION", "The form has validation errors. No submission was attempted.")
                screenshot = await page.screenshot(full_page=True)
                pre_submit_text = (await page.locator("body").inner_text())[:20000]
                # Mark the attempt before invoking click: Playwright may time out after dispatching it.
                clicked = True
                await page.locator("form button[type=submit],form input[type=submit],form button:not([type])").click(timeout=self.timeout_ms)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                await page.wait_for_timeout(750)
                text = (await page.locator("body").inner_text())[:20000]
                screenshot = await page.screenshot(full_page=True)
                confirmation = self._confirmation(plan.adapter, text)
                prior_confirmation = self._confirmation(plan.adapter, pre_submit_text)
                if confirmation and not prior_confirmation:
                    number = ""
                    import re
                    match = re.search(r"(?:confirmation|application)\s*(?:number|id|#)\s*[:#-]?\s*([A-Z0-9-]{4,})", confirmation, re.I)
                    number = match.group(1) if match else ""
                    return BrowserSubmissionResult(status="CONFIRMED_SUCCESS", reason_code="CONFIRMATION_VISIBLE", confirmation_marker=confirmation[:500], confirmation_number=number, screenshot_bytes=screenshot, submission_clicked=True)
                return BrowserSubmissionResult(status="OUTCOME_UNKNOWN", reason_code="NO_EXPLICIT_CONFIRMATION", screenshot_bytes=screenshot, submission_clicked=True)
            except SubmissionBlocked:
                raise
            except Exception:
                return BrowserSubmissionResult(status="OUTCOME_UNKNOWN" if clicked else "CONFIRMED_FAILURE", reason_code="BROWSER_ERROR_AFTER_CLICK" if clicked else "BROWSER_ERROR_BEFORE_CLICK", screenshot_bytes=screenshot, submission_clicked=clicked)
            finally:
                await browser.close()

    @staticmethod
    def _locator(page, key: str):
        kind, _, value = key.partition(":")
        if kind == "id":
            return page.locator(f"#{value}")
        if kind == "name":
            return page.locator(f'[name="{value}"]')
        raise SubmissionBlocked("UNLOCATABLE_FIELD", "A form field has no stable identifier; manual application is required.")

    @staticmethod
    async def _install_request_guard(page, allowed_hosts: set[str]) -> None:
        async def guard(route):
            if _host_allowed(route.request.url, allowed_hosts):
                await route.continue_()
            else:
                await route.abort("blockedbyclient")

        await page.route("**/*", guard)

    @staticmethod
    async def _snapshot_current_page(page) -> FormSnapshot:
        result = await page.evaluate("""() => {
          const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
          const forms = [...document.forms], form = forms.length === 1 ? forms[0] : null;
          const fields = form ? [...form.querySelectorAll('input,textarea,select')].map((e,i) => ({key:e.id?`id:${e.id}`:e.name?`name:${e.name}`:`index:${i}`,label:(e.labels?.[0]?.innerText||e.getAttribute('aria-label')||e.getAttribute('placeholder')||'').trim().slice(0,300),control_type:e.tagName==='TEXTAREA'?'textarea':e.tagName==='SELECT'?'select':(e.type||'text'),required:!!e.required||e.getAttribute('aria-required')==='true',visible:visible(e),options:e.tagName==='SELECT'?[...e.options].map(o=>o.text.trim()).slice(0,100):[]})) : [];
          return {current_url:location.href,page_text:document.body?.innerText?.slice(0,20000)||'',form_count:forms.length,submit_button_count:form?form.querySelectorAll('button[type=submit],input[type=submit],button:not([type])').length:0,form_action:form?.action||'',form_method:form?.method||'',fields,frame_urls:[...document.querySelectorAll('iframe')].map(f=>f.src).slice(0,50)};
        }""")
        return FormSnapshot.model_validate(result)

    @staticmethod
    def _confirmation(adapter: str, text: str) -> str:
        import re
        patterns = {
            "greenhouse": (r"application (?:has been )?submitted", r"thank you for applying", r"application received"),
            "lever": (r"application (?:has been )?submitted", r"thank you for applying", r"thanks for applying"),
        }[adapter]
        for pattern in patterns:
            found = re.search(pattern, text, re.I)
            if found:
                return text[max(0, found.start()-80):found.end()+160]
        return ""
