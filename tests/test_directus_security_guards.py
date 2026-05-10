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

    _node(
        f"""
        import registerEndpoint from {source_url!r};

        const routes = new Map();
        const router = {{
          use() {{}},
          get(path, fn) {{ routes.set(path, fn); }},
          post() {{}},
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
            users_registered: 95,
            users_used_trial: 78,
            users_key_activated: 47,
            users_active_subscription: 22,
            first_seen: '2026-05-10T00:00:00Z',
            last_seen: '2026-06-15T12:34:56Z',
          }},
          {{
            utm: null,
            users_total: 4500,
            users_registered: 3200,
            users_used_trial: 2100,
            users_key_activated: 1800,
            users_active_subscription: 950,
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
        if (captured.sources[0].revenue_rub !== 0) throw new Error('revenue_rub must default to 0 without payments table');
        if (typeof captured.totals?.users_total !== 'number') throw new Error('totals.users_total must be a number');
        if (captured.filters_applied?.utm_prefix !== 'qr_') throw new Error('filters_applied.utm_prefix not echoed');
        if (captured.filters_applied?.limit !== 50) throw new Error('filters_applied.limit not coerced to number');
        if (!captured.generated_at) throw new Error('generated_at missing');
        """
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
