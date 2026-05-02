from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _node(script: str) -> None:
    subprocess.run(
        ["node", "--input-type=module", "-"],
        input=textwrap.dedent(script),
        text=True,
        cwd=ROOT,
        check=True,
    )


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_admin_widgets_rejects_non_admin_before_routes():
    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()
    legacy_source = _read("directus/extensions/endpoints/admin-widgets/index.js")
    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")

    assert "function isAdminRequest(req)" in legacy_source
    assert "router.use((req, res, next)" in legacy_source
    assert "Admin access required" in dist_source

    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const middleware = [];
        const routes = [];
        const router = {{
          use(fn) {{ middleware.push(fn); }},
          get(path, fn) {{ routes.push(['GET', path, fn]); }},
          post(path, fn) {{ routes.push(['POST', path, fn]); }},
        }};

        registerEndpoint(router, {{ database: () => {{ throw new Error('database must not run during guard smoke'); }} }});
        if (middleware.length !== 1) throw new Error(`expected one auth middleware, got ${{middleware.length}}`);
        if (routes.length === 0) throw new Error('expected admin widgets routes to be registered');

        let nonAdminStatus = 0;
        let nonAdminPayload = null;
        let nonAdminNext = false;
        await middleware[0](
          {{ accountability: {{ user: 'editor' }} }},
          {{
            status(code) {{ nonAdminStatus = code; return this; }},
            json(payload) {{ nonAdminPayload = payload; return payload; }},
          }},
          () => {{ nonAdminNext = true; }},
        );
        if (nonAdminStatus !== 403) throw new Error(`expected 403 for non-admin, got ${{nonAdminStatus}}`);
        if (nonAdminPayload?.error !== 'Admin access required') throw new Error('unexpected non-admin payload');
        if (nonAdminNext) throw new Error('non-admin request reached next()');

        let adminNext = false;
        await middleware[0](
          {{ accountability: {{ admin: true }} }},
          {{ status() {{ throw new Error('admin request must not be rejected'); }} }},
          () => {{ adminNext = true; }},
        );
        if (!adminNext) throw new Error('admin request did not reach next()');
        """
    )


def test_tariff_preview_proxy_is_admin_only():
    endpoint_url = (ROOT / "directus/extensions/endpoints/tariff-studio/index.js").as_uri()

    _node(
        f"""
        import registerEndpoint from {endpoint_url!r};

        let handler = null;
        const router = {{
          post(path, fn) {{
            if (path !== '/quote-preview') throw new Error(`unexpected path ${{path}}`);
            handler = fn;
          }},
        }};
        registerEndpoint(router);
        if (!handler) throw new Error('quote-preview handler was not registered');

        process.env.ADMIN_INTEGRATION_URL = 'https://backend.example';
        process.env.ADMIN_INTEGRATION_TOKEN = 'token';
        let fetchCalled = false;
        globalThis.fetch = async () => {{
          fetchCalled = true;
          return {{
            ok: true,
            text: async () => JSON.stringify({{ ok: true, preview: [] }}),
          }};
        }};

        let status = 200;
        let payload = null;
        await handler(
          {{ accountability: {{ user: 'editor' }}, body: {{ patch: {{ price: 1 }} }} }},
          {{
            status(code) {{ status = code; return this; }},
            json(body) {{ payload = body; return body; }},
          }},
        );
        if (status !== 403) throw new Error(`expected 403 for non-admin, got ${{status}}`);
        if (payload?.message !== 'Admin access required') throw new Error('unexpected non-admin payload');
        if (fetchCalled) throw new Error('non-admin request called backend integration');

        status = 200;
        payload = null;
        await handler(
          {{ accountability: {{ admin: true }}, body: {{ tariff_id: 1, patch: {{ price: 1 }} }} }},
          {{
            status(code) {{ status = code; return this; }},
            json(body) {{ payload = body; return body; }},
          }},
        );
        if (status !== 200) throw new Error(`expected admin status 200, got ${{status}}`);
        if (!fetchCalled) throw new Error('admin request did not call backend integration');
        if (payload?.ok !== true) throw new Error('unexpected admin payload');
        """
    )


def test_promo_hmac_hook_fails_closed_without_secret_and_hashes_with_secret():
    hook_url = (ROOT / "directus/extensions/promo-code-hmac/src/index.js").as_uri()
    legacy_source = _read("directus/extensions/hooks/promo-code-hmac/index.js")
    dist_source = _read("directus/extensions/promo-code-hmac/dist/index.js")

    assert "PROMO_HMAC_SECRET is not configured" in legacy_source
    assert "PROMO_HMAC_SECRET is not configured" in dist_source

    _node(
        f"""
        import registerHook from {hook_url!r};

        function createFilter(secret) {{
          if (secret === undefined) {{
            delete process.env.PROMO_HMAC_SECRET;
          }} else {{
            process.env.PROMO_HMAC_SECRET = secret;
          }}
          let createHandler = null;
          registerHook({{
            filter(name, handler) {{
              if (name === 'items.create') createHandler = handler;
            }},
          }});
          if (!createHandler) throw new Error('items.create filter was not registered');
          return createHandler;
        }}

        const withoutSecret = createFilter(undefined);
        let rejected = false;
        try {{
          await withoutSecret({{ raw_code: 'WELCOME2026' }}, {{ collection: 'promo_codes' }});
        }} catch (error) {{
          rejected = String(error?.message || error).includes('PROMO_HMAC_SECRET is not configured');
        }}
        if (!rejected) throw new Error('raw promo code was not rejected without secret');

        const untouched = await withoutSecret({{ raw_code: 'WELCOME2026' }}, {{ collection: 'users' }});
        if (untouched.raw_code !== 'WELCOME2026') throw new Error('non-promo collection was changed');

        const withSecret = createFilter('local-test-secret');
        const hashed = await withSecret({{ raw_code: 'WELCOME2026' }}, {{ collection: 'promo_codes' }});
        if ('raw_code' in hashed) throw new Error('raw_code was not removed after hashing');
        if (!/^[0-9a-f]{{64}}$/.test(hashed.code_hmac || '')) throw new Error('code_hmac is not a sha256 hex hmac');
        """
    )
