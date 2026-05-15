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
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")

    # Guard the canonical top-level package (Directus 11 only loads these,
    # the legacy by-type endpoints/<name>/index.js layout was purged).
    assert "function isAdminRequest(req)" in src_source
    assert "router.use((req, res, next)" in src_source
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
          patch(path, fn) {{ routes.push(['PATCH', path, fn]); }},
          delete(path, fn) {{ routes.push(['DELETE', path, fn]); }},
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


def test_admin_widgets_user_card_route_registered_and_validates_input():
    """The /user-card/:user_id route must be present in src + dist, validate ids and return 404 for missing users."""

    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")

    assert '"/user-card/:user_id"' in src_source, "user-card route missing from src"
    assert "/user-card/" in dist_source, "user-card route missing from dist"
    assert "Invalid user id" in src_source
    assert "User not found" in src_source

    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get(path, fn) {{ routes.set(path, fn); }},
          post() {{}},
          patch() {{}},
          delete() {{}},
        }};

        const fakeBuilder = () => {{
          const qb = {{
            where() {{ return qb; }},
            select() {{ return qb; }},
            orderBy() {{ return qb; }},
            limit() {{ return qb; }},
            count() {{ return qb; }},
            min() {{ return qb; }},
            first: async () => null,
          }};
          return qb;
        }};
        const fakeDatabase = (table) => fakeBuilder();
        fakeDatabase.raw = (sql) => sql;

        registerEndpoint(router, {{ database: fakeDatabase }});
        const handler = routes.get('/user-card/:user_id');
        if (!handler) throw new Error('user-card route was not registered');

        // Invalid id: non-numeric must return 400 BEFORE touching DB.
        let invalidStatus = 0;
        let invalidPayload = null;
        await handler(
          {{ params: {{ user_id: 'abc' }} }},
          {{
            status(code) {{ invalidStatus = code; return this; }},
            json(payload) {{ invalidPayload = payload; return payload; }},
          }},
        );
        if (invalidStatus !== 400) throw new Error(`expected 400 for invalid id, got ${{invalidStatus}}`);
        if (!String(invalidPayload?.error).includes('Invalid user id')) throw new Error('unexpected invalid-id payload');

        // Missing user: information_schema lookup returns null, then users lookup returns null → 404.
        const presentTablesRouter = routes.get('/user-card/:user_id');
        let missingStatus = 0;
        let missingPayload = null;
        const tableAwareDatabase = (table) => {{
          const qb = {{
            where() {{ return qb; }},
            select() {{ return qb; }},
            orderBy() {{ return qb; }},
            limit() {{ return qb; }},
            count() {{ return qb; }},
            min() {{ return qb; }},
            first: async () => {{
              if (table === 'information_schema.tables') {{
                return {{ table_name: 'users' }};
              }}
              if (table === 'users') {{
                return null;
              }}
              return null;
            }},
          }};
          return qb;
        }};
        tableAwareDatabase.raw = (sql) => sql;

        const router2 = {{
          use() {{}},
          get(path, fn) {{ if (path === '/user-card/:user_id') routes.set(path, fn); }},
          post() {{}},
          patch() {{}},
          delete() {{}},
        }};
        registerEndpoint(router2, {{ database: tableAwareDatabase }});
        const handler2 = routes.get('/user-card/:user_id');

        await handler2(
          {{ params: {{ user_id: '12345' }} }},
          {{
            status(code) {{ missingStatus = code; return this; }},
            json(payload) {{ missingPayload = payload; return payload; }},
          }},
        );
        if (missingStatus !== 404) throw new Error(`expected 404 for missing user, got ${{missingStatus}}`);
        if (!String(missingPayload?.error).includes('User not found')) throw new Error('unexpected missing-user payload');
        """
    )


def test_tvpn_user_card_interface_extension_loads():
    """The tvpn-user-card interface bundle must declare the right id/types and call the admin-widgets endpoint.

    The dist imports from `@directus/extensions-sdk` and `vue`, which are host-provided
    externals that only resolve inside the Directus admin runtime — therefore the
    extension is validated statically here. ``node --check`` is used to confirm the
    dist parses as valid JavaScript.
    """

    dist_path = ROOT / "directus/extensions/tvpn-user-card/dist/index.js"
    src_source = _read("directus/extensions/tvpn-user-card/src/index.js")
    interface_source = _read("directus/extensions/tvpn-user-card/src/interface.vue")
    dist_source = _read("directus/extensions/tvpn-user-card/dist/index.js")

    assert 'id: "tvpn-user-card"' in src_source
    assert "tvpn-user-card" in dist_source
    assert '"alias"' in src_source
    assert "/admin-widgets/user-card" in src_source
    # The Vue side must call the admin-widgets endpoint and not embed a hard-coded base URL.
    assert "endpointBase" in interface_source
    assert "useApi" in interface_source
    # Sanity: the bundle must reach for useApi (admin-widgets call) and define the interface id.
    assert "useApi" in dist_source
    assert '"tvpn-user-card"' in dist_source or "'tvpn-user-card'" in dist_source

    subprocess.run(
        ["node", "--check", str(dist_path)],
        cwd=ROOT,
        check=True,
    )


def test_tariff_preview_proxy_is_admin_only():
    endpoint_url = (ROOT / "directus/extensions/tariff-studio/src/index.js").as_uri()
    src_source = _read("directus/extensions/tariff-studio/src/index.js")
    dist_source = _read("directus/extensions/tariff-studio/dist/index.js")

    assert "Admin access required" in src_source
    assert "Admin access required" in dist_source

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
    src_source = _read("directus/extensions/promo-code-hmac/src/index.js")
    dist_source = _read("directus/extensions/promo-code-hmac/dist/index.js")

    assert "PROMO_HMAC_SECRET is not configured" in src_source
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


def test_admin_widgets_utm_stats_route_registered_and_returns_shape():
    """The /utm-stats route must be present in src + dist, group users by utm,
    and return the expected JSON shape (sources/totals/filters_applied/generated_at)."""

    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")

    assert '"/utm-stats"' in src_source, "utm-stats route missing from src"
    assert "/utm-stats" in dist_source, "utm-stats route missing from dist"
    assert "users_active_subscription" in src_source
    assert "Failed to build utm-stats payload" in src_source
    # Direct/indirect must be derived from referrer-utm equality (SELF JOIN),
    # NOT from `referred_by IS NULL` which is wrong for partner-attributed
    # campaigns where every UTM visitor is also linked to a partner.
    assert "LEFT JOIN users r ON r.id = u.referred_by" in src_source, (
        "direct/indirect split must SELF JOIN with referrer to detect inherited tags"
    )
    assert "LEFT JOIN users r2 ON r2.id = u2.referred_by" in src_source, (
        "payments CTE must also SELF JOIN with referrer for direct/indirect split"
    )
    assert "r.utm = u.utm" in src_source, (
        "indirect = referrer.utm matches user.utm (inheritance from PR feat/acquisition-source-attribution)"
    )

    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get(path, fn) {{ routes.set(path, fn); }},
          post() {{}},
          patch() {{}},
          delete() {{}},
        }};

        // Fake database: only `users` table exists; processed_payments / columns
        // calls return empty so the no-payments branch of the SQL is exercised.
        const fakeDatabase = (table) => {{
          const qb = {{
            select() {{ return qb; }},
            where(conds) {{
              if (conds && typeof conds === 'object' && conds.table_name === 'users') {{
                qb.first = async () => ({{ table_name: 'users' }});
              }} else {{
                qb.first = async () => null;
              }}
              return qb;
            }},
            count() {{ return qb; }},
            sum() {{ return qb; }},
            first: async () => null,
          }};
          return qb;
        }};
        const groupedRows = [
          {{
            utm: 'qr_rt_launch_2026_05',
            users_total: 142,
            users_direct: 100,
            users_indirect: 42,
            users_registered: 95,
            users_used_trial: 78,
            users_key_activated: 47,
            users_active_subscription: 22,
            users_active_subscription_direct: 15,
            users_active_subscription_indirect: 7,
            first_seen: '2026-05-10T00:00:00Z',
            last_seen: '2026-06-15T12:34:56Z',
          }},
          {{
            utm: null,
            users_total: 4500,
            users_direct: 4500,
            users_indirect: 0,
            users_registered: 3200,
            users_used_trial: 2100,
            users_key_activated: 1800,
            users_active_subscription: 950,
            users_active_subscription_direct: 950,
            users_active_subscription_indirect: 0,
            first_seen: '2024-01-01T00:00:00Z',
            last_seen: '2026-06-20T00:00:00Z',
          }},
        ];
        fakeDatabase.raw = async (sql) => {{
          if (sql.includes('grouped AS')) return {{ rows: groupedRows }};
          return {{
            rows: [
              {{ users_total: 4642, users_with_utm: 142, users_no_utm: 4500 }},
            ],
          }};
        }};

        registerEndpoint(router, {{ database: fakeDatabase }});
        const handler = routes.get('/utm-stats');
        if (!handler) throw new Error('utm-stats route was not registered');

        let captured = null;
        await handler(
          {{ query: {{ since: '2026-05-01', utm_prefix: 'qr_', limit: '50' }} }},
          {{
            json(payload) {{ captured = payload; return payload; }},
            status() {{ throw new Error('handler returned non-200 status'); }},
          }},
        );
        if (!captured) throw new Error('handler did not respond');
        if (!Array.isArray(captured.sources)) throw new Error('sources missing or not array');
        if (captured.sources.length !== 2) throw new Error('expected 2 sources rows');
        if (captured.sources[0].utm !== 'qr_rt_launch_2026_05') throw new Error('first source must be top-utm');
        if (captured.sources[1].utm !== null) throw new Error('second source utm must normalize to null');
        if (captured.sources[0].users_paid !== 0) throw new Error('users_paid must default to 0 without payments table');
        if (captured.sources[0].users_paid_direct !== 0) throw new Error('users_paid_direct must default to 0 without payments table');
        if (captured.sources[0].users_paid_indirect !== 0) throw new Error('users_paid_indirect must default to 0 without payments table');
        if (captured.sources[0].revenue_rub !== 0) throw new Error('revenue_rub must default to 0 without payments table');
        if (captured.sources[0].revenue_rub_direct !== 0) throw new Error('revenue_rub_direct must default to 0 without payments table');
        if (captured.sources[0].revenue_rub_indirect !== 0) throw new Error('revenue_rub_indirect must default to 0 without payments table');
        if (captured.sources[0].users_direct !== 100) throw new Error('users_direct must be passed through');
        if (captured.sources[0].users_indirect !== 42) throw new Error('users_indirect must be passed through');
        if (captured.sources[0].users_active_subscription_direct !== 15) throw new Error('users_active_subscription_direct must be passed through');
        if (captured.sources[0].users_active_subscription_indirect !== 7) throw new Error('users_active_subscription_indirect must be passed through');
        if (typeof captured.totals?.users_total !== 'number') throw new Error('totals.users_total must be a number');
        if (captured.filters_applied?.utm_prefix !== 'qr_') throw new Error('filters_applied.utm_prefix not echoed');
        if (captured.filters_applied?.limit !== 50) throw new Error('filters_applied.limit not coerced to number');
        if (!captured.generated_at) throw new Error('generated_at missing');
        """
    )


def test_admin_widgets_utm_stats_timeseries_route_registered():
    """The /utm-stats/timeseries route must be present in src + dist, accept
    `utm` + `bucket` params, and reject missing utm with 400."""

    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")

    assert '"/utm-stats/timeseries"' in src_source, "timeseries route missing from src"
    assert "/utm-stats/timeseries" in dist_source, "timeseries route missing from dist"
    assert "date_trunc" in src_source, "timeseries must bucket via date_trunc"
    assert "utm or utm_prefix is required" in src_source

    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get(path, fn) {{ routes.set(path, fn); }},
          post() {{}},
          patch() {{}},
          delete() {{}},
        }};

        const fakeDatabase = (table) => {{
          const qb = {{
            select() {{ return qb; }},
            where(conds) {{
              if (conds && typeof conds === 'object' && conds.table_name === 'users') {{
                qb.first = async () => ({{ table_name: 'users' }});
              }} else {{
                qb.first = async () => null;
              }}
              return qb;
            }},
            count() {{ return qb; }},
            sum() {{ return qb; }},
            first: async () => null,
          }};
          return qb;
        }};
        fakeDatabase.raw = async (sql) => ({{
          rows: [
            {{ bucket_ts: '2026-05-01T00:00:00Z', registrations: 7, paid_count: 1, revenue_rub: 150 }},
            {{ bucket_ts: '2026-05-02T00:00:00Z', registrations: 12, paid_count: 3, revenue_rub: 450 }},
          ],
        }});

        registerEndpoint(router, {{ database: fakeDatabase }});
        const handler = routes.get('/utm-stats/timeseries');
        if (!handler) throw new Error('timeseries route was not registered');

        // Missing utm → 400.
        let badStatus = 0;
        let badPayload = null;
        await handler(
          {{ query: {{}} }},
          {{
            status(code) {{ badStatus = code; return this; }},
            json(payload) {{ badPayload = payload; return payload; }},
          }},
        );
        if (badStatus !== 400) throw new Error(`expected 400 for missing utm, got ${{badStatus}}`);
        if (!String(badPayload?.error).includes('utm or utm_prefix is required')) throw new Error('unexpected missing-utm payload');

        // Valid utm + week bucket → buckets returned.
        let captured = null;
        await handler(
          {{ query: {{ utm: 'qr_rt_launch_2026_05', bucket: 'week', since: '2026-05-01' }} }},
          {{
            json(payload) {{ captured = payload; return payload; }},
            status() {{ throw new Error('valid request must not be rejected'); }},
          }},
        );
        if (!captured) throw new Error('handler did not respond');
        if (captured.utm !== 'qr_rt_launch_2026_05') throw new Error('utm not echoed');
        if (captured.bucket !== 'week') throw new Error('bucket not coerced to week');
        if (!Array.isArray(captured.buckets) || captured.buckets.length !== 2) throw new Error('buckets missing or wrong shape');
        if (captured.buckets[0].registrations !== 7) throw new Error('registration count not passed through');
        if (captured.buckets[1].revenue_rub !== 450) throw new Error('revenue not passed through');
        """
    )


def test_admin_widgets_utm_stats_funnel_route_registered():
    """The /utm-stats/funnel route must be present, require utm, and compute
    step-to-step + step-to-total ratios from the SQL aggregate row."""

    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")

    assert '"/utm-stats/funnel"' in src_source, "funnel route missing from src"
    assert "/utm-stats/funnel" in dist_source, "funnel route missing from dist"
    assert "ratio_total" in src_source
    assert "ratio_prev" in src_source

    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get(path, fn) {{ routes.set(path, fn); }},
          post() {{}},
          patch() {{}},
          delete() {{}},
        }};

        const fakeDatabase = (table) => {{
          const qb = {{
            select() {{ return qb; }},
            where(conds) {{
              if (conds && typeof conds === 'object' && conds.table_name === 'users') {{
                qb.first = async () => ({{ table_name: 'users' }});
              }} else {{
                qb.first = async () => null;
              }}
              return qb;
            }},
            count() {{ return qb; }},
            sum() {{ return qb; }},
            first: async () => null,
          }};
          return qb;
        }};
        fakeDatabase.raw = async (sql) => ({{
          rows: [{{
            total: 100,
            registered: 75,
            used_trial: 60,
            key_activated: 35,
            active_subscription: 18,
            paid: 0,
          }}],
        }});

        registerEndpoint(router, {{ database: fakeDatabase }});
        const handler = routes.get('/utm-stats/funnel');
        if (!handler) throw new Error('funnel route was not registered');

        let captured = null;
        await handler(
          {{ query: {{ utm: 'qr_rt_launch_2026_05' }} }},
          {{
            json(payload) {{ captured = payload; return payload; }},
            status() {{ throw new Error('valid request must not be rejected'); }},
          }},
        );
        if (!captured) throw new Error('handler did not respond');
        if (captured.utm !== 'qr_rt_launch_2026_05') throw new Error('utm not echoed');
        if (!Array.isArray(captured.steps) || captured.steps.length !== 6) throw new Error('expected 6 funnel steps');
        if (captured.steps[0].key !== 'total' || captured.steps[0].count !== 100) throw new Error('step 0 must be total=100');
        if (captured.steps[1].key !== 'registered' || captured.steps[1].count !== 75) throw new Error('step 1 must be registered=75');
        if (Math.abs(captured.steps[1].ratio_total - 0.75) > 0.001) throw new Error('step 1 ratio_total wrong');
        if (Math.abs(captured.steps[1].ratio_prev - 0.75) > 0.001) throw new Error('step 1 ratio_prev wrong (75/100)');
        if (Math.abs(captured.steps[2].ratio_prev - 0.80) > 0.001) throw new Error('step 2 ratio_prev wrong (60/75)');
        if (captured.steps[5].count !== 0) throw new Error('paid step missing');
        """
    )


def test_admin_widgets_utm_stats_cohort_route_registered():
    """The /utm-stats/cohort route must be present, require utm, and bucket by week."""

    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")

    assert '"/utm-stats/cohort"' in src_source, "cohort route missing from src"
    assert "/utm-stats/cohort" in dist_source, "cohort route missing from dist"
    assert "date_trunc('week'" in src_source, "cohort must bucket by week"
    assert "ratio_paid" in src_source

    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get(path, fn) {{ routes.set(path, fn); }},
          post() {{}},
          patch() {{}},
          delete() {{}},
        }};

        const fakeDatabase = (table) => {{
          const qb = {{
            select() {{ return qb; }},
            where(conds) {{
              if (conds && typeof conds === 'object' && conds.table_name === 'users') {{
                qb.first = async () => ({{ table_name: 'users' }});
              }} else {{
                qb.first = async () => null;
              }}
              return qb;
            }},
            count() {{ return qb; }},
            sum() {{ return qb; }},
            first: async () => null,
          }};
          return qb;
        }};
        fakeDatabase.raw = async (sql) => ({{
          rows: [
            {{ cohort_week: '2026-05-01T00:00:00Z', cohort_size: 120, registered: 80, trial: 60, activated: 30, active_now: 14, paid: 5 }},
            {{ cohort_week: '2026-04-24T00:00:00Z', cohort_size: 95, registered: 70, trial: 50, activated: 22, active_now: 12, paid: 3 }},
          ],
        }});

        registerEndpoint(router, {{ database: fakeDatabase }});
        const handler = routes.get('/utm-stats/cohort');
        if (!handler) throw new Error('cohort route not registered');

        // Missing utm → 400.
        let badStatus = 0;
        await handler(
          {{ query: {{}} }},
          {{ status(code) {{ badStatus = code; return this; }}, json() {{ return {{}}; }} }},
        );
        if (badStatus !== 400) throw new Error('missing utm must 400');

        let captured = null;
        await handler(
          {{ query: {{ utm: 'qr_rt_launch_2026_05', weeks: '8' }} }},
          {{ json(p) {{ captured = p; return p; }}, status() {{ throw new Error('rejected'); }} }},
        );
        if (!captured) throw new Error('no payload');
        if (!Array.isArray(captured.cohorts) || captured.cohorts.length !== 2) throw new Error('cohorts shape');
        const first = captured.cohorts[0];
        if (first.cohort_size !== 120) throw new Error('cohort_size missing');
        if (Math.abs(first.ratio_paid - 5/120) > 0.0001) throw new Error('ratio_paid wrong');
        if (Math.abs(first.ratio_registered - 80/120) > 0.0001) throw new Error('ratio_registered wrong');
        """
    )


def test_admin_widgets_utm_campaigns_crud_routes_registered():
    """utm-campaigns CRUD routes must be present and reject invalid utm."""

    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")

    assert '"/utm-campaigns"' in src_source, "utm-campaigns list route missing"
    assert '"/utm-campaigns/by-utm/:utm"' in src_source, "utm-campaigns by-utm route missing"
    assert '"/utm-campaigns/:id"' in src_source, "utm-campaigns update/delete route missing"
    assert "utm_campaigns" in dist_source
    assert "Campaign already exists" in src_source

    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get(path, fn) {{ routes.set('GET ' + path, fn); }},
          post(path, fn) {{ routes.set('POST ' + path, fn); }},
          patch(path, fn) {{ routes.set('PATCH ' + path, fn); }},
          delete(path, fn) {{ routes.set('DELETE ' + path, fn); }},
        }};

        // Fake database. Only `utm_campaigns` answers as existing table.
        const tableState = {{ utm_campaigns: [] }};
        function makeQb(table) {{
          const qb = {{
            _conds: null,
            _table: table,
            select() {{ return qb; }},
            where(conds) {{
              if (typeof conds === 'function') {{
                // Ignore search builder callbacks in the fake.
                return qb;
              }}
              if (conds && typeof conds === 'object' && conds.table_name === 'utm_campaigns') {{
                qb.first = async () => ({{ table_name: 'utm_campaigns' }});
                qb.then = (resolve) => resolve([{{ table_name: 'utm_campaigns' }}]);
              }} else if (table === 'utm_campaigns') {{
                qb._conds = conds;
                qb.first = async () => tableState.utm_campaigns.find((r) => Object.keys(conds||{{}}).every((k) => r[k] === conds[k])) || null;
                qb.then = (resolve) => resolve(
                  tableState.utm_campaigns.filter((r) => Object.keys(conds||{{}}).every((k) => r[k] === conds[k]))
                );
              }} else {{
                qb.first = async () => null;
                qb.then = (resolve) => resolve([]);
              }}
              return qb;
            }},
            orderBy() {{ return qb; }},
            limit() {{ return qb; }},
            count() {{ return qb; }},
            sum() {{ return qb; }},
            first: async () => null,
            update: async () => undefined,
            // Thenable so `await fakeDatabase(table).select().orderBy().limit()` resolves to rows.
            then(resolve) {{
              resolve(table === 'utm_campaigns' ? tableState.utm_campaigns : []);
            }},
          }};
          qb.insert = (row) => {{
            const arr = Array.isArray(row) ? row : [row];
            for (const r of arr) {{
              if (table === 'utm_campaigns') {{
                tableState.utm_campaigns.push({{ id: tableState.utm_campaigns.length + 1, ...r }});
              }}
            }}
            return {{
              returning: async () => arr.map(() => tableState.utm_campaigns[tableState.utm_campaigns.length - 1]),
            }};
          }};
          return qb;
        }}
        const fakeDatabase = (table) => makeQb(table);
        fakeDatabase.raw = async () => ({{ rows: [] }});
        fakeDatabase.fn = {{ now: () => new Date() }};
        fakeDatabase.schema = {{ hasTable: async () => true, createTable: async () => undefined }};

        registerEndpoint(router, {{ database: fakeDatabase }});
        const listHandler = routes.get('GET /utm-campaigns');
        if (!listHandler) throw new Error('list route missing');
        const createHandler = routes.get('POST /utm-campaigns');
        if (!createHandler) throw new Error('create route missing');
        const updateHandler = routes.get('PATCH /utm-campaigns/:id');
        if (!updateHandler) throw new Error('update route missing');
        const deleteHandler = routes.get('DELETE /utm-campaigns/:id');
        if (!deleteHandler) throw new Error('delete route missing');

        // List should return [] initially.
        let captured;
        await listHandler(
          {{ query: {{ status: 'active' }} }},
          {{ json(p) {{ captured = p; return p; }}, status() {{ throw new Error('list rejected'); }} }},
        );
        if (!Array.isArray(captured?.campaigns)) throw new Error('list shape wrong');

        // Create with invalid utm → 400.
        let badStatus = 0;
        let badPayload = null;
        await createHandler(
          {{ body: {{ utm: 'bad utm with space' }}, accountability: {{ user: 'admin' }} }},
          {{
            status(code) {{ badStatus = code; return this; }},
            json(p) {{ badPayload = p; return p; }},
          }},
        );
        if (badStatus !== 400) throw new Error(`expected 400 for bad utm, got ${{badStatus}}`);
        if (!String(badPayload?.error).includes('utm must match')) throw new Error('bad payload: ' + String(badPayload?.error));

        // Create with reserved partner → 400.
        badStatus = 0; badPayload = null;
        await createHandler(
          {{ body: {{ utm: 'partner' }}, accountability: {{ user: 'admin' }} }},
          {{
            status(code) {{ badStatus = code; return this; }},
            json(p) {{ badPayload = p; return p; }},
          }},
        );
        if (badStatus !== 400) throw new Error('reserved utm must 400');
        """
    )


def test_tvpn_utm_campaigns_bootstrap_extension_loads():
    """The bootstrap hook must compile and reference the campaigns table."""

    dist_path = ROOT / "directus/extensions/tvpn-utm-campaigns-bootstrap/dist/index.js"
    src_source = _read("directus/extensions/tvpn-utm-campaigns-bootstrap/src/index.js")

    assert "utm_campaigns" in src_source
    assert "app.after" in src_source
    assert "directus_collections" in src_source
    assert "directus_fields" in src_source

    subprocess.run(
        ["node", "--check", str(dist_path)],
        cwd=ROOT,
        check=True,
    )


def test_tvpn_utm_stats_module_extension_loads():
    """The tvpn-utm-stats module bundle must declare the right id and call /admin-widgets/utm-stats."""

    dist_path = ROOT / "directus/extensions/tvpn-utm-stats/dist/index.js"
    src_source = _read("directus/extensions/tvpn-utm-stats/src/index.js")
    module_source = _read("directus/extensions/tvpn-utm-stats/src/module.vue")
    dist_source = _read("directus/extensions/tvpn-utm-stats/dist/index.js")

    assert 'id: ROUTE_PATH' in src_source or '"tvpn-utm-stats"' in src_source
    assert "tvpn-utm-stats" in dist_source
    assert "/admin-widgets/utm-stats" in module_source
    assert "useApi" in module_source
    assert "useApi" in dist_source

    subprocess.run(
        ["node", "--check", str(dist_path)],
        cwd=ROOT,
        check=True,
    )


def test_admin_widgets_user_card_attribution_chain_payload():
    """The /user-card/:user_id handler must walk referred_by upward, count downstream
    descendants, and surface an `attribution` block — the display-side companion of
    the inheritance logic in bloobcat/funcs/referral_attribution.py."""

    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")

    assert "WITH RECURSIVE chain" in src_source, "attribution chain CTE missing from src"
    assert "WITH RECURSIVE descendants" in src_source, "downstream CTE missing from src"
    assert "downstream_count" in src_source
    assert "attribution" in src_source
    assert "WITH RECURSIVE chain" in dist_source, "attribution chain CTE missing from dist (rebuild required)"
    assert "WITH RECURSIVE descendants" in dist_source, "downstream CTE missing from dist (rebuild required)"

    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get(path, fn) {{ routes.set(path, fn); }},
          post() {{}},
          patch() {{}},
          delete() {{}},
        }};

        const userRow = {{
          id: 200,
          username: 'invited',
          full_name: 'Invited User',
          utm: 'qr_rt_launch_2026_05',
          referred_by: 100,
          referrals: 3,
          referral_bonus_days_total: 0,
          custom_referral_percent: 0,
          is_partner: false,
          is_admin: false,
          is_registered: true,
          is_subscribed: false,
          is_blocked: false,
          is_trial: false,
          used_trial: false,
          key_activated: false,
          balance: 0,
          partner_link_mode: null,
          remnawave_uuid: null,
          email: null,
          language_code: 'ru',
          registration_date: '2026-05-09T00:00:00Z',
          created_at: '2026-05-09T00:00:00Z',
          connected_at: null,
          blocked_at: null,
          trial_started_at: null,
          last_hwid_reset: null,
          last_failed_message_at: null,
          failed_message_count: 0,
          prize_wheel_attempts: 0,
          device_per_user_enabled: null,
          expired_at: null,
          active_tariff_id: null,
          lte_gb_total: 0,
        }};
        const referrerRow = {{
          id: 100,
          full_name: 'Campaign Source',
          username: 'campaign',
          is_partner: false,
        }};
        const tableAwareDatabase = (table) => {{
          let matchedTable = table;
          let whereConds = null;
          const qb = {{
            select() {{ return qb; }},
            where(conds) {{ whereConds = conds; return qb; }},
            orderBy() {{ return qb; }},
            limit() {{ return qb; }},
            count() {{ return qb; }},
            sum() {{ return qb; }},
            min() {{ return qb; }},
            first: async () => {{
              if (matchedTable === 'information_schema.tables') return {{ table_name: whereConds?.table_name }};
              if (matchedTable === 'information_schema.columns') return {{ column_name: whereConds?.column_name }};
              if (matchedTable === 'users') {{
                if (whereConds?.id === '200' || whereConds?.id === 200) return userRow;
                if (whereConds?.id === 100 || whereConds?.id === '100') return referrerRow;
                return null;
              }}
              return null;
            }},
            // Thenable for awaited chains that don't end in .first() — knex builders
            // are thenable and resolve to an array; we mirror that with [].
            then(onFulfilled) {{ return Promise.resolve([]).then(onFulfilled); }},
          }};
          return qb;
        }};
        tableAwareDatabase.raw = async (sql) => {{
          const text = String(sql);
          if (text.includes('WITH RECURSIVE chain')) {{
            return {{
              rows: [
                {{
                  id: 100,
                  referred_by: null,
                  utm: 'qr_rt_launch_2026_05',
                  full_name: 'Campaign Source',
                  username: 'campaign',
                  is_partner: false,
                  depth: 1,
                }},
              ],
            }};
          }}
          if (text.includes('WITH RECURSIVE descendants')) {{
            return {{ rows: [{{ count: 7 }}] }};
          }}
          return {{ rows: [] }};
        }};

        registerEndpoint(router, {{ database: tableAwareDatabase }});
        const handler = routes.get('/user-card/:user_id');
        if (!handler) throw new Error('user-card route was not registered');

        let captured = null;
        let capturedStatus = 200;
        await handler(
          {{ params: {{ user_id: '200' }} }},
          {{
            status(code) {{ capturedStatus = code; return this; }},
            json(payload) {{ captured = payload; return payload; }},
          }},
        );
        if (capturedStatus !== 200) throw new Error(`expected 200, got ${{capturedStatus}}: ${{JSON.stringify(captured)}}`);
        if (!captured) throw new Error('handler did not respond');
        const attribution = captured.referrals?.attribution;
        if (!attribution) throw new Error('referrals.attribution block missing');
        if (attribution.source !== 'inherited') throw new Error(`expected source=inherited, got ${{attribution.source}}`);
        if (!Array.isArray(attribution.chain) || attribution.chain.length !== 1) throw new Error('chain must contain exactly 1 ancestor');
        const ancestor = attribution.chain[0];
        if (ancestor.id !== '100') throw new Error('ancestor id must be stringified');
        if (ancestor.utm !== 'qr_rt_launch_2026_05') throw new Error('ancestor utm not propagated');
        if (ancestor.utm_is_campaign !== true) throw new Error('ancestor.utm_is_campaign must be true');
        if (!attribution.inherited_from || attribution.inherited_from.id !== '100') throw new Error('inherited_from must point at the matching ancestor');
        if (!attribution.campaign_root || attribution.campaign_root.id !== '100') throw new Error('campaign_root must be exposed');
        if (attribution.own_is_campaign !== true) throw new Error('own_is_campaign must be true for the campaign tag');
        if (captured.referrals.downstream_count !== 7) throw new Error(`downstream_count must be 7, got ${{captured.referrals.downstream_count}}`);
        """
    )


def test_tvpn_user_card_bootstrap_hook_inserts_field_when_missing():
    """The bootstrap hook must idempotently ensure a presentation field on the
    `users` collection bound to interface=`tvpn-user-card`. First boot inserts;
    second boot must NOT overwrite (so manual operator tweaks survive)."""

    src_path = ROOT / "directus/extensions/tvpn-user-card-bootstrap/src/index.js"
    dist_path = ROOT / "directus/extensions/tvpn-user-card-bootstrap/dist/index.js"
    pkg_path = ROOT / "directus/extensions/tvpn-user-card-bootstrap/package.json"
    src_source = src_path.read_text(encoding="utf-8")
    pkg_source = pkg_path.read_text(encoding="utf-8")

    assert '"type": "hook"' in pkg_source, "bootstrap must register as a hook extension"
    assert "tvpn-user-card" in src_source
    assert "directus_fields" in src_source
    assert "init(\"app.after\"" in src_source, "must hook on app.after to run after Directus boot"

    subprocess.run(["node", "--check", str(dist_path)], cwd=ROOT, check=True)

    source_url = src_path.as_uri()
    _node(
        f"""
        import registerHook from {source_url!r};

        // Capture mode: track .where() conds and .insert() payloads.
        let lookups = 0;
        let inserts = [];
        let lookupResult = null;

        const fakeDatabase = (table) => {{
          let conds = null;
          const qb = {{
            where(c) {{ conds = c; return qb; }},
            first: async () => {{
              if (table !== 'directus_fields') throw new Error('unexpected table: ' + table);
              lookups += 1;
              return lookupResult;
            }},
            insert: async (row) => {{
              if (table !== 'directus_fields') throw new Error('unexpected insert table: ' + table);
              inserts.push({{ row, conds }});
            }},
          }};
          return qb;
        }};

        const initHandlers = new Map();
        const hookContext = {{
          init(event, fn) {{ initHandlers.set(event, fn); }},
          filter() {{}},
          action() {{}},
          schedule() {{}},
          embed() {{}},
        }};
        const accessors = {{ database: fakeDatabase, logger: {{ info() {{}}, error() {{}}, warn() {{}} }} }};

        registerHook(hookContext, accessors);
        const handler = initHandlers.get('app.after');
        if (!handler) throw new Error('app.after not registered');

        // First boot: directus_fields has no row for users.tvpn_user_card_presentation → insert.
        lookupResult = null;
        await handler({{ }});
        if (lookups !== 1) throw new Error(`expected 1 lookup, got ${{lookups}}`);
        if (inserts.length !== 1) throw new Error(`expected 1 insert, got ${{inserts.length}}`);
        const inserted = inserts[0].row;
        if (inserted.collection !== 'users') throw new Error('collection must be users');
        if (inserted.field !== 'tvpn_user_card_presentation') throw new Error('field name must be the presentation alias');
        if (inserted.interface !== 'tvpn-user-card') throw new Error('interface must be tvpn-user-card');
        if (!inserted.special.includes('alias')) throw new Error('special must mark this as alias');
        const opts = JSON.parse(inserted.options);
        if (opts.endpoint !== '/admin-widgets/user-card') throw new Error('endpoint option must point at admin-widgets');

        // Second boot: row exists → NO insert (idempotency + no clobber).
        lookupResult = {{ id: 42, collection: 'users', field: 'tvpn_user_card_presentation', interface: 'tvpn-user-card' }};
        inserts = [];
        await handler({{ }});
        if (inserts.length !== 0) throw new Error(`second boot must not insert when field exists, got ${{inserts.length}} inserts`);
        """
    )


def test_admin_widgets_user_card_attribution_handles_organic_user():
    """Organic users (no referrer, no utm) must yield attribution.source='organic'
    and an empty chain — and the referred_by-gated chain CTE must not be issued."""

    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()

    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get(path, fn) {{ routes.set(path, fn); }},
          post() {{}},
          patch() {{}},
          delete() {{}},
        }};

        const userRow = {{
          id: 9001,
          username: 'organic',
          full_name: 'Organic',
          utm: null,
          referred_by: null,
          referrals: 0,
          referral_bonus_days_total: 0,
          custom_referral_percent: 0,
          is_partner: false,
          is_admin: false,
          is_registered: true,
          is_subscribed: false,
          is_blocked: false,
          is_trial: false,
          used_trial: false,
          key_activated: false,
          balance: 0,
          partner_link_mode: null,
          remnawave_uuid: null,
          email: null,
          language_code: 'ru',
          registration_date: null,
          created_at: '2026-05-10T00:00:00Z',
          connected_at: null,
          blocked_at: null,
          trial_started_at: null,
          last_hwid_reset: null,
          last_failed_message_at: null,
          failed_message_count: 0,
          prize_wheel_attempts: 0,
          device_per_user_enabled: null,
          expired_at: null,
          active_tariff_id: null,
          lte_gb_total: 0,
        }};
        const tableAwareDatabase = (table) => {{
          let matchedTable = table;
          let whereConds = null;
          const qb = {{
            select() {{ return qb; }},
            where(conds) {{ whereConds = conds; return qb; }},
            orderBy() {{ return qb; }},
            limit() {{ return qb; }},
            count() {{ return qb; }},
            sum() {{ return qb; }},
            min() {{ return qb; }},
            first: async () => {{
              if (matchedTable === 'information_schema.tables') return {{ table_name: whereConds?.table_name }};
              if (matchedTable === 'information_schema.columns') return {{ column_name: whereConds?.column_name }};
              if (matchedTable === 'users') return userRow;
              return null;
            }},
            then(onFulfilled) {{ return Promise.resolve([]).then(onFulfilled); }},
          }};
          return qb;
        }};
        let chainCalled = false;
        tableAwareDatabase.raw = async (sql) => {{
          const text = String(sql);
          if (text.includes('WITH RECURSIVE chain')) {{ chainCalled = true; return {{ rows: [] }}; }}
          if (text.includes('WITH RECURSIVE descendants')) return {{ rows: [{{ count: 0 }}] }};
          return {{ rows: [] }};
        }};

        registerEndpoint(router, {{ database: tableAwareDatabase }});
        const handler = routes.get('/user-card/:user_id');
        let captured = null;
        let capturedStatus = 200;
        await handler(
          {{ params: {{ user_id: '9001' }} }},
          {{
            status(code) {{ capturedStatus = code; return this; }},
            json(payload) {{ captured = payload; return payload; }},
          }},
        );
        if (capturedStatus !== 200) throw new Error(`expected 200, got ${{capturedStatus}}: ${{JSON.stringify(captured)}}`);
        if (chainCalled) throw new Error('chain CTE must be skipped when referred_by is null');
        const attribution = captured.referrals?.attribution;
        if (!attribution) throw new Error('attribution block missing');
        if (attribution.source !== 'organic') throw new Error(`expected source=organic, got ${{attribution.source}}`);
        if (attribution.chain.length !== 0) throw new Error('chain must be empty for organic users');
        if (captured.referrals.downstream_count !== 0) throw new Error('downstream_count must be 0 for organic users with no descendants');
        """
    )


def test_admin_widgets_descendants_cte_has_depth_cap():
    """The recursive descendants CTE must be depth-capped to prevent DoS on adversarial chains.

    The ascendants CTE (chain) is already capped at ATTRIBUTION_CHAIN_MAX_DEPTH=10;
    descendants now mirrors that protection via DOWNSTREAM_MAX_DEPTH so an attacker
    cannot loop or fan out without bound.
    """
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")

    # The constant must exist alongside the CTE so the cap can be tuned in one place.
    assert "DOWNSTREAM_MAX_DEPTH" in src_source
    # The CTE itself must reference depth in both WHERE clauses (descendants vs chain
    # walk) — without it, an adversarial cycle would loop forever.
    # Identifier names get minified in dist but the SQL literal survives the rollup.
    assert "d.depth + 1" in src_source
    assert "d.depth <" in src_source
    assert "d.depth + 1" in dist_source
    assert "d.depth <" in dist_source


def test_admin_widgets_user_card_rejects_negative_ids():
    """user-card/:user_id now accepts positive integers only.

    Telegram user ids and Vectra web ids are always >= 1; the previous
    /^-?\\d+$/ regex let through `-1` which would silently fail downstream
    lookups but consume DB time.
    """
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    assert r"/^\d+$/" in src_source
    assert "/^-?\\d+$/" not in src_source

    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()
    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get(path, fn) {{ routes.set(path, fn); }},
          post() {{}},
          patch() {{}},
          delete() {{}},
        }};

        const fakeBuilder = () => {{
          const qb = {{
            where() {{ return qb; }}, select() {{ return qb; }},
            orderBy() {{ return qb; }}, limit() {{ return qb; }},
            count() {{ return qb; }}, min() {{ return qb; }},
            first: async () => null,
          }};
          return qb;
        }};
        const fakeDatabase = (table) => fakeBuilder();
        fakeDatabase.raw = (sql) => sql;

        registerEndpoint(router, {{ database: fakeDatabase }});
        const handler = routes.get('/user-card/:user_id');

        for (const bad of ['-1', '-9001', '-0']) {{
          let status = 0;
          let payload = null;
          await handler(
            {{ params: {{ user_id: bad }} }},
            {{
              status(code) {{ status = code; return this; }},
              json(p) {{ payload = p; return p; }},
            }},
          );
          if (status !== 400) throw new Error(`expected 400 for ${{bad}}, got ${{status}}`);
          if (!String(payload?.error).includes('Invalid user id')) {{
            throw new Error(`unexpected payload for ${{bad}}: ${{JSON.stringify(payload)}}`);
          }}
        }}
        """
    )


def test_admin_widgets_utm_campaigns_validate_extended_payload():
    """validateCampaignPayload rejects oversized/typed-wrong inputs added in 1.5.0.

    Previously only utm + label + status were size-bounded; description, notes,
    promo_code, partner_user_id and tags fell through to the DB layer with
    raw 500s instead of 400s, and there was no upper bound on free-form text.
    """
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    # Sanity: all new bounds are present in source AND in built dist.
    for needle in (
        "description must be <= 2000 chars",
        "notes must be <= 5000 chars",
        "promo_code must be <= 100 chars",
        "partner_user_id must be a positive integer",
        "tags must be an array",
        "tags must have <= 50 items",
        "tags items must be <= 50 chars",
    ):
        assert needle in src_source, f"missing validation message: {needle}"

    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")
    assert "description must be <= 2000 chars" in dist_source
    assert "tags must have <= 50 items" in dist_source

    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()
    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get() {{}},
          post(path, fn) {{ routes.set(path, fn); }},
          patch() {{}},
          delete() {{}},
        }};

        const fakeBuilder = () => {{
          const qb = {{
            where() {{ return qb; }}, select() {{ return qb; }},
            orderBy() {{ return qb; }}, limit() {{ return qb; }},
            count() {{ return qb; }}, min() {{ return qb; }},
            first: async () => null,
            insert() {{ return {{ returning: async () => [] }}; }},
          }};
          return qb;
        }};
        const fakeDatabase = (table) => fakeBuilder();
        fakeDatabase.raw = (sql) => sql;
        // hasTable() returns true via information_schema lookup
        fakeDatabase.__hasTable = true;

        // The /utm-campaigns POST route does a hasTable check first; stub it to true.
        const origDatabase = fakeDatabase;
        const tableAwareDatabase = (table) => {{
          const qb = origDatabase(table);
          if (table === 'information_schema.tables') {{
            qb.first = async () => ({{ table_name: 'utm_campaigns' }});
          }}
          return qb;
        }};
        tableAwareDatabase.raw = (sql) => sql;

        registerEndpoint(router, {{ database: tableAwareDatabase }});
        const handler = routes.get('/utm-campaigns');
        if (!handler) throw new Error('POST /utm-campaigns not registered');

        async function call(body) {{
          let status = 200;
          let payload = null;
          await handler(
            {{ body, accountability: {{ admin: true, user: 'admin' }} }},
            {{
              status(code) {{ status = code; return this; }},
              json(p) {{ payload = p; return p; }},
            }},
          );
          return {{ status, payload }};
        }}

        const cases = [
          [{{ utm: 'ok', description: 'x'.repeat(2001) }}, 'description must be <= 2000 chars'],
          [{{ utm: 'ok', notes: 'x'.repeat(5001) }}, 'notes must be <= 5000 chars'],
          [{{ utm: 'ok', promo_code: 'BAD!!CHAR' }}, 'promo_code must match'],
          [{{ utm: 'ok', promo_code: 'A'.repeat(101) }}, 'promo_code must be <= 100 chars'],
          [{{ utm: 'ok', partner_user_id: -1 }}, 'partner_user_id must be a positive integer'],
          [{{ utm: 'ok', partner_user_id: 1.5 }}, 'partner_user_id must be a positive integer'],
          [{{ utm: 'ok', tags: 'not-array' }}, 'tags must be an array'],
          [{{ utm: 'ok', tags: Array(51).fill('x') }}, 'tags must have <= 50 items'],
          [{{ utm: 'ok', tags: ['x'.repeat(51)] }}, 'tags items must be <= 50 chars'],
        ];

        for (const [body, needle] of cases) {{
          const {{ status, payload }} = await call(body);
          if (status !== 400) {{
            throw new Error(`expected 400 for ${{JSON.stringify(body).slice(0,80)}}, got ${{status}}: ${{JSON.stringify(payload)}}`);
          }}
          if (!String(payload?.error).includes(needle)) {{
            throw new Error(`expected error containing "${{needle}}", got: ${{JSON.stringify(payload)}}`);
          }}
        }}
        """
    )


def test_users_lte_grant_stamp_hook_stamps_on_first_grant():
    """Defense-in-depth: when an admin sets users.lte_gb_total via Directus UI
    (bypassing the backend sync_user_lte service), the hook must stamp
    admin_lte_granted_at on first grant so the LTE limiter anchors the quota
    window to the grant moment rather than `created_at`.
    """
    src_source = _read("directus/extensions/users-lte-grant-stamp/src/index.js")
    dist_source = _read("directus/extensions/users-lte-grant-stamp/dist/index.js")

    # Static guards on the contract — strings survive minification.
    assert 'collection !== "users"' in src_source
    assert "admin_lte_granted_at" in src_source
    assert "admin_lte_granted_at" in dist_source

    source_url = (ROOT / "directus/extensions/users-lte-grant-stamp/src/index.js").as_uri()
    _node(
        f"""
        import registerHook from {source_url!r};

        const filters = [];
        const filterApi = {{
          filter(event, fn) {{ filters.push({{ event, fn }}); }},
        }};
        registerHook(filterApi);
        const updateHook = filters.find((f) => f.event === 'items.update');
        if (!updateHook) throw new Error('items.update filter not registered');

        // Case 1: not the users collection — no-op.
        const otherCollection = await updateHook.fn({{ lte_gb_total: 50 }}, {{ collection: 'promo_codes', keys: [1] }});
        if (otherCollection.admin_lte_granted_at) {{
          throw new Error('hook must not stamp for non-users collection');
        }}

        // Case 2: users update but lte_gb_total not in payload — no-op.
        const noLte = await updateHook.fn({{ email: 'a@b.c' }}, {{ collection: 'users', keys: [1] }});
        if (noLte.admin_lte_granted_at) {{
          throw new Error('hook must not stamp when lte_gb_total absent');
        }}

        // Case 3: lte_gb_total = 0 — clearing a grant, not granting → no-op.
        const zeroLte = await updateHook.fn({{ lte_gb_total: 0 }}, {{ collection: 'users', keys: [1] }});
        if (zeroLte.admin_lte_granted_at) {{
          throw new Error('hook must not stamp when lte_gb_total is 0');
        }}

        // Case 4: lte_gb_total > 0 AND target row has NULL stamp → STAMP.
        const fakeDatabase = (table) => {{
          if (table !== 'users') throw new Error('unexpected table: ' + table);
          return {{
            whereIn() {{ return this; }},
            select: async () => [{{ id: 7, admin_lte_granted_at: null }}],
          }};
        }};
        const ctx = {{ database: fakeDatabase }};
        const stamped = await updateHook.fn(
          {{ lte_gb_total: 50 }},
          {{ collection: 'users', keys: [7] }},
          ctx,
        );
        if (!stamped.admin_lte_granted_at) {{
          throw new Error('hook MUST stamp admin_lte_granted_at when lte_gb_total>0 and existing stamp is NULL');
        }}
        if (Number.isNaN(Date.parse(stamped.admin_lte_granted_at))) {{
          throw new Error('stamp must be a valid ISO timestamp');
        }}

        // Case 5: target row already has a stamp → do NOT re-stamp (preserve anchor).
        const alreadyStampedDatabase = (table) => ({{
          whereIn() {{ return this; }},
          select: async () => [{{ id: 7, admin_lte_granted_at: '2026-01-01T00:00:00Z' }}],
        }});
        const preserved = await updateHook.fn(
          {{ lte_gb_total: 100 }},
          {{ collection: 'users', keys: [7] }},
          {{ database: alreadyStampedDatabase }},
        );
        if (preserved.admin_lte_granted_at !== undefined) {{
          throw new Error('hook must NOT overwrite an existing admin_lte_granted_at');
        }}

        // Case 6: explicit admin_lte_granted_at in payload — respect it.
        const explicit = await updateHook.fn(
          {{ lte_gb_total: 50, admin_lte_granted_at: '2026-05-12T00:00:00Z' }},
          {{ collection: 'users', keys: [7] }},
          ctx,
        );
        if (explicit.admin_lte_granted_at !== '2026-05-12T00:00:00Z') {{
          throw new Error('hook must respect explicit admin_lte_granted_at in payload');
        }}

        // Case 7: mixed batch (one NULL + one stamped) → skip (avoid sliding existing anchor).
        const mixedDatabase = (table) => ({{
          whereIn() {{ return this; }},
          select: async () => [
            {{ id: 1, admin_lte_granted_at: null }},
            {{ id: 2, admin_lte_granted_at: '2026-04-01T00:00:00Z' }},
          ],
        }});
        const mixed = await updateHook.fn(
          {{ lte_gb_total: 50 }},
          {{ collection: 'users', keys: [1, 2] }},
          {{ database: mixedDatabase }},
        );
        if (mixed.admin_lte_granted_at !== undefined) {{
          throw new Error('hook must skip stamping for mixed batches');
        }}
        """
    )


def test_admin_widgets_users_lookup_finds_oauth_users_by_email():
    """`/users/lookup` must search both users.email and auth_identities.email,
    merge results, sort telegram before web, and report empty-query as empty."""

    source_url = (ROOT / "directus/extensions/admin-widgets/src/index.js").as_uri()
    src_source = _read("directus/extensions/admin-widgets/src/index.js")
    dist_source = _read("directus/extensions/admin-widgets/dist/index.js")

    assert '"/users/lookup"' in src_source, "users/lookup route missing from src"
    assert "users/lookup" in dist_source, "users/lookup route missing from dist"
    assert "Failed to look up users" in src_source

    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get(path, fn) {{ routes.set(path, fn); }},
          post() {{}},
          patch() {{}},
          delete() {{}},
        }};

        // Fake knex builder: each `database(table)` call returns a
        // chainable mock; `await qb` resolves to a canned result keyed by
        // the table name and accumulated WHERE shape. Good enough to drive
        // the four code paths we care about.
        function makeDb(scenario) {{
          const fakeDb = (table) => {{
            const state = {{ table, joined: null, whereRaw: [] }};
            const qb = {{
              innerJoin(other) {{ state.joined = other; return qb; }},
              select() {{ return qb; }},
              limit() {{ return qb; }},
              orderBy() {{ return qb; }},
              where(builder) {{ if (typeof builder === 'function') builder({{
                orWhere() {{ return this; }},
                orWhereRaw(_sql, params) {{ state.whereRaw.push(params); return this; }},
              }}); return qb; }},
              whereRaw(_sql, params) {{ state.whereRaw.push(params); return qb; }},
              whereIn(_col, values) {{ state.whereIn = values; return qb; }},
              first: async () => {{
                if (table === 'information_schema.tables') return {{ table_name: 'x' }};
                return null;
              }},
              then(resolve, reject) {{
                try {{
                  resolve(scenario(state));
                }} catch (err) {{
                  reject(err);
                }}
              }},
            }};
            return qb;
          }};
          fakeDb.raw = (sql) => sql;
          return fakeDb;
        }}

        // Scenario A: query "test@example.com" — match in users.email AND
        // a second match in auth_identities.email pointing to a different
        // (web) user. Expect 2 matches, telegram-id first.
        const scenarioA = (state) => {{
          if (state.table === 'information_schema.tables') return [];
          if (state.table === 'users') {{
            return [
              {{ id: 12345, username: 'tg_user', full_name: 'Telegram User', email: 'test@example.com', registration_date: '2026-05-01T00:00:00Z' }},
            ];
          }}
          if (state.table === 'auth_identities AS ai') {{
            return [
              {{ id: 8500000000000001, username: null, full_name: 'Google User', email: 'test@example.com', registration_date: '2026-05-10T00:00:00Z', provider: 'google', provider_email: 'test@example.com' }},
            ];
          }}
          if (state.table === 'auth_identities') {{
            // Second-pass enrichment for already-matched user ids.
            return [
              {{ user_id: 12345, provider: 'telegram', email: null }},
              {{ user_id: 8500000000000001, provider: 'google', email: 'test@example.com' }},
            ];
          }}
          return [];
        }};

        registerEndpoint(router, {{ database: makeDb(scenarioA) }});
        const handler = routes.get('/users/lookup');
        if (!handler) throw new Error('users/lookup handler not registered');

        let statusCode = 200;
        let body = null;
        await handler(
          {{ query: {{ q: 'test@example.com' }} }},
          {{
            status(code) {{ statusCode = code; return this; }},
            json(payload) {{ body = payload; return payload; }},
          }},
        );
        if (statusCode !== 200) throw new Error(`scenario A expected 200, got ${{statusCode}}`);
        if (!body) throw new Error('scenario A: no body');
        if (body.query !== 'test@example.com') throw new Error(`scenario A: query echo wrong: ${{body.query}}`);
        if (!Array.isArray(body.matches)) throw new Error('scenario A: matches not array');
        if (body.matches.length !== 2) throw new Error(`scenario A: expected 2 matches, got ${{body.matches.length}}`);
        // Telegram id (< 8e15) must be first.
        if (Number(body.matches[0].user_id) >= 8_000_000_000_000_000) {{
          throw new Error('scenario A: telegram user must sort before web user');
        }}
        // Providers must be aggregated from enrichment pass.
        const webRow = body.matches[1];
        if (!webRow.providers.includes('google')) {{
          throw new Error('scenario A: web user must have google provider listed');
        }}
        if (webRow.user_card_url !== '/admin/content/users/8500000000000001') {{
          throw new Error(`scenario A: user_card_url wrong: ${{webRow.user_card_url}}`);
        }}

        // Scenario B: empty query → empty matches, no DB hit beyond table check.
        const router2 = {{ use() {{}}, get(path, fn) {{ routes.set(path, fn); }}, post() {{}}, patch() {{}}, delete() {{}} }};
        registerEndpoint(router2, {{ database: makeDb(() => []) }});
        const handler2 = routes.get('/users/lookup');
        let statusB = 0;
        let bodyB = null;
        await handler2(
          {{ query: {{ q: '   ' }} }},
          {{
            status(code) {{ statusB = code; return this; }},
            json(payload) {{ bodyB = payload; return payload; }},
          }},
        );
        if (statusB !== 200 && statusB !== 0) throw new Error(`scenario B expected 200 default, got ${{statusB}}`);
        if (!bodyB || !Array.isArray(bodyB.matches) || bodyB.matches.length !== 0) {{
          throw new Error('scenario B: empty-query must return empty matches array');
        }}

        // Scenario C: no users.email match, but auth_identities.email match → still 1 result.
        const scenarioC = (state) => {{
          if (state.table === 'information_schema.tables') return [];
          if (state.table === 'users') return [];
          if (state.table === 'auth_identities AS ai') {{
            return [
              {{ id: 8500000000000002, username: null, full_name: 'Apple User', email: null, registration_date: '2026-04-20T00:00:00Z', provider: 'apple', provider_email: 'apple@example.com' }},
            ];
          }}
          if (state.table === 'auth_identities') {{
            return [{{ user_id: 8500000000000002, provider: 'apple', email: 'apple@example.com' }}];
          }}
          return [];
        }};
        const routesC = new Map();
        const routerC = {{ use() {{}}, get(path, fn) {{ routesC.set(path, fn); }}, post() {{}}, patch() {{}}, delete() {{}} }};
        registerEndpoint(routerC, {{ database: makeDb(scenarioC) }});
        const handlerC = routesC.get('/users/lookup');
        let bodyC = null;
        await handlerC(
          {{ query: {{ q: 'apple@example.com' }} }},
          {{ status() {{ return this; }}, json(payload) {{ bodyC = payload; return payload; }} }},
        );
        if (!bodyC || bodyC.matches.length !== 1) {{
          throw new Error(`scenario C: expected 1 match, got ${{bodyC?.matches?.length}}`);
        }}
        if (bodyC.matches[0].providers[0] !== 'apple') {{
          throw new Error('scenario C: provider must be "apple"');
        }}
        if (!bodyC.matches[0].provider_emails.includes('apple@example.com')) {{
          throw new Error('scenario C: provider_emails must contain matched email');
        }}

        // Scenario D: no matches anywhere → empty array, 200.
        const scenarioD = () => [];
        const routesD = new Map();
        const routerD = {{ use() {{}}, get(path, fn) {{ routesD.set(path, fn); }}, post() {{}}, patch() {{}}, delete() {{}} }};
        registerEndpoint(routerD, {{ database: makeDb(scenarioD) }});
        const handlerD = routesD.get('/users/lookup');
        let bodyD = null;
        await handlerD(
          {{ query: {{ q: 'unknown@example.com' }} }},
          {{ status() {{ return this; }}, json(payload) {{ bodyD = payload; return payload; }} }},
        );
        if (!bodyD || bodyD.matches.length !== 0) {{
          throw new Error('scenario D: expected empty matches');
        }}
        """
    )
