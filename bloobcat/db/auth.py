from __future__ import annotations

from tortoise import fields, models


class AuthIdentity(models.Model):
    id = fields.IntField(primary_key=True)
    user = fields.ForeignKeyField(
        "models.Users", related_name="auth_identities", on_delete=fields.CASCADE
    )
    provider = fields.CharField(max_length=32)
    provider_subject = fields.CharField(max_length=255)
    email = fields.CharField(max_length=255, null=True)
    email_verified = fields.BooleanField(default=False)
    display_name = fields.CharField(max_length=255, null=True)
    avatar_url = fields.TextField(null=True)
    linked_at = fields.DatetimeField(auto_now_add=True)
    last_login_at = fields.DatetimeField(null=True)

    class Meta:
        table = "auth_identities"
        unique_together = (("provider", "provider_subject"),)
        indexes = (("user_id", "provider"), ("provider", "provider_subject"))


class AuthPasswordCredential(models.Model):
    id = fields.IntField(primary_key=True)
    user = fields.ForeignKeyField(
        "models.Users", related_name="password_credentials", on_delete=fields.CASCADE
    )
    email_normalized = fields.CharField(max_length=255, unique=True)
    password_hash = fields.CharField(max_length=255)
    email_verified = fields.BooleanField(default=False)
    verification_token_hash = fields.CharField(max_length=128, null=True, unique=True)
    verification_expires_at = fields.DatetimeField(null=True)
    reset_token_hash = fields.CharField(max_length=128, null=True, unique=True)
    reset_expires_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "auth_password_credentials"
        indexes = (("user_id",), ("email_normalized",), ("verification_token_hash",), ("reset_token_hash",))


class AuthOAuthState(models.Model):
    id = fields.IntField(primary_key=True)
    state_hash = fields.CharField(max_length=128, unique=True)
    provider = fields.CharField(max_length=32)
    mode = fields.CharField(max_length=16)
    nonce = fields.CharField(max_length=128)
    pkce_verifier = fields.CharField(max_length=255)
    linking_user_id = fields.BigIntField(null=True)
    return_to = fields.CharField(max_length=512, null=True)
    # Captured from the URL the user landed on (?start=...). Persisted on the
    # state row so the OAuth callback can apply referral attribution at
    # user-creation time — before `/auth/complete-registration` runs and
    # before the admin notification fires. Bounded length matches the
    # start_param length we accept from /auth/telegram and bot /start.
    start_param = fields.CharField(max_length=256, null=True)
    expires_at = fields.DatetimeField()
    consumed_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "auth_oauth_states"
        indexes = (("provider", "mode"), ("expires_at",), ("linking_user_id",))


class AuthLoginTicket(models.Model):
    id = fields.IntField(primary_key=True)
    ticket_hash = fields.CharField(max_length=128, unique=True)
    user = fields.ForeignKeyField(
        "models.Users", related_name="auth_login_tickets", on_delete=fields.CASCADE
    )
    expires_at = fields.DatetimeField()
    consumed_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "auth_login_tickets"
        indexes = (("user_id",), ("expires_at",),)


class AuthLinkRequest(models.Model):
    id = fields.IntField(primary_key=True)
    token_hash = fields.CharField(max_length=128, unique=True)
    source_user = fields.ForeignKeyField(
        "models.Users", related_name="auth_link_requests", on_delete=fields.CASCADE
    )
    target_provider = fields.CharField(max_length=32)
    expires_at = fields.DatetimeField()
    consumed_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "auth_link_requests"
        indexes = (("source_user_id",), ("target_provider",), ("expires_at",))


class AuthAuditEvent(models.Model):
    id = fields.IntField(primary_key=True)
    user_id = fields.BigIntField(null=True)
    provider = fields.CharField(max_length=32, null=True)
    action = fields.CharField(max_length=64)
    result = fields.CharField(max_length=32)
    reason = fields.CharField(max_length=128, null=True)
    ip_hash = fields.CharField(max_length=128, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "auth_audit_events"
        indexes = (("user_id",), ("provider", "action"), ("created_at",))
