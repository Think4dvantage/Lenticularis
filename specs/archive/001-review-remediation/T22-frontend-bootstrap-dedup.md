# T22 — De-duplicate frontend nav/bootstrap boilerplate

**Severity:** Medium · **Phase:** 4 · **Model tier:** Larger (do incrementally)

## Ground Rules
- Read `.ai/instructions/03-frontend-conventions.md`. LF line endings only. **No build step / npm.**
- Keep all `console.*` logging with `[Lenti:<page>]` prefixes. Behavior-preserving.

## Problem
Across ~9 HTML pages, the nav markup (~15–19 lines), nav/lang-picker CSS (~59 lines), and the i18n +
nav bootstrap (`await initI18n(); renderNavAuth(...); renderLangPicker();`) are copy-pasted. Several
pages also inline an `isLoggedIn() ? fetchAuth : fetch` fallback even though `auth.js` exports
`fetchAuth`.

## Fix — no-build, incremental
1. **Shared bootstrap helper.** In `static/app.js` (or a new `static/bootstrap.js`), add a single
   `async function bootstrapPage(opts)` that runs `initI18n()`, mounts the nav (`renderNavAuth`),
   and mounts the lang picker (`renderLangPicker`) — the exact sequence pages repeat today. Export it
   (ES module). Page module scripts then call `await bootstrapPage({...})` instead of repeating the
   three calls.
2. **Shared nav injection.** Move the duplicated nav markup into a JS function (e.g.
   `renderNav(container)`) in the shared module that injects the `<nav>` HTML into a known mount
   point (`<div id="appNav"></div>`). Replace the inline `<nav>…</nav>` block in each page with the
   mount div + a `renderNav` call inside `bootstrapPage`. Keep the lang-picker mount (`#navLangPicker`)
   contract intact.
3. **Shared nav CSS.** Move the repeated nav/lang-picker CSS into `static/shared.css` (already linked
   on every page) and delete the per-page copies.
4. **Use `fetchAuth` everywhere.** Replace inline `isLoggedIn() ? fetchAuth : fetch` fallbacks with a
   direct `fetchAuth` import where the call is authenticated; leave genuinely public calls on `fetch`.

Do this **one page at a time**, verifying the page renders identically (nav, language switch, auth
state) after each migration. Start with two pages, confirm, then proceed.

## Constraints
- Do not change the visual design tokens (dark theme colors) — see `03-frontend-conventions.md`.
- Do not add a bundler or `package.json`.
- i18n: every visible string still resolves via `window.t(...)` with keys in all 4 locales.

## Acceptance criteria
- Nav markup + nav CSS exist in one shared place; per-page copies are removed for migrated pages.
- Each migrated page calls `bootstrapPage(...)` instead of repeating `initI18n/renderNavAuth/renderLangPicker`.
- Pages render identically (nav, lang picker, auth state, theme) and the console shows the
  `[Lenti:<page>]` logs.
