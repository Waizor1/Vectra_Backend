from __future__ import annotations

import pytest

from bloobcat.settings import RemnaWaveSettings


def test_remnawave_settings_blank_optional_uuid_values_become_none():
    settings = RemnaWaveSettings.model_validate(
        {
            "url": "https://pan.example.test",
            "token": "token",
            "default_internal_squad_uuid": " ",
            "default_external_squad_uuid": "",
            "lte_internal_squad_uuid": None,
        }
    )

    assert settings.default_internal_squad_uuid is None
    assert settings.default_external_squad_uuid is None
    assert settings.lte_internal_squad_uuid is None


def test_remnawave_settings_rejects_invalid_optional_uuid_value():
    with pytest.raises(ValueError):
        RemnaWaveSettings.model_validate(
            {
                "url": "https://pan.example.test",
                "token": "token",
                "default_external_squad_uuid": "not-a-uuid",
            }
        )


def test_remnawave_settings_rejects_strata_panel_url():
    with pytest.raises(ValueError, match="Strata RemnaWave"):
        RemnaWaveSettings.model_validate(
            {
                "url": "https://pan.stratavpn.com",
                "token": "token",
            }
        )
