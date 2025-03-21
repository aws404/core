"""Tests for Philips Hue config flow."""
import asyncio
from unittest.mock import Mock, patch

from aiohue.discovery import URL_NUPNP
from aiohue.errors import LinkButtonNotPressed
import pytest
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import ssdp, zeroconf
from homeassistant.components.hue import config_flow, const
from homeassistant.components.hue.errors import CannotConnect

from tests.common import MockConfigEntry


@pytest.fixture(name="hue_setup", autouse=True)
def hue_setup_fixture():
    """Mock hue entry setup."""
    with patch("homeassistant.components.hue.async_setup_entry", return_value=True):
        yield


def get_discovered_bridge(bridge_id="aabbccddeeff", host="1.2.3.4", supports_v2=False):
    """Return a mocked Discovered Bridge."""
    return Mock(host=host, id=bridge_id, supports_v2=supports_v2)


def create_mock_api_discovery(aioclient_mock, bridges):
    """Patch aiohttp responses with fake data for bridge discovery."""
    aioclient_mock.get(
        URL_NUPNP,
        json=[{"internalipaddress": host, "id": id} for (host, id) in bridges],
    )
    for (host, bridge_id) in bridges:
        aioclient_mock.get(
            f"http://{host}/api/config",
            json={"bridgeid": bridge_id},
        )
        # mock v2 support if v2 found in id
        aioclient_mock.get(
            f"https://{host}/clip/v2/resources",
            status=403 if "v2" in bridge_id else 404,
        )


async def test_flow_works(hass):
    """Test config flow ."""
    disc_bridge = get_discovered_bridge(supports_v2=True)

    with patch(
        "homeassistant.components.hue.config_flow.discover_nupnp",
        return_value=[disc_bridge],
    ):
        result = await hass.config_entries.flow.async_init(
            const.DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "init"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"id": disc_bridge.id}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "link"

    flow = next(
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["flow_id"] == result["flow_id"]
    )
    assert flow["context"]["unique_id"] == "aabbccddeeff"

    with patch.object(config_flow, "create_app_key", return_value="123456789"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "Hue Bridge aabbccddeeff"
    assert result["data"] == {
        "host": "1.2.3.4",
        "api_key": "123456789",
        "api_version": 2,
    }


async def test_manual_flow_works(hass):
    """Test config flow discovers only already configured bridges."""
    disc_bridge = get_discovered_bridge(bridge_id="id-1234", host="2.2.2.2")

    MockConfigEntry(
        domain="hue", source=config_entries.SOURCE_IGNORE, unique_id="bla"
    ).add_to_hass(hass)

    with patch(
        "homeassistant.components.hue.config_flow.discover_nupnp",
        return_value=[disc_bridge],
    ):
        result = await hass.config_entries.flow.async_init(
            const.DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "init"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"id": "manual"}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "manual"

    with patch.object(config_flow, "discover_bridge", return_value=disc_bridge):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "2.2.2.2"}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "link"

    with patch.object(config_flow, "create_app_key", return_value="123456789"), patch(
        "homeassistant.components.hue.async_unload_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == "create_entry"
    assert result["title"] == f"Hue Bridge {disc_bridge.id}"
    assert result["data"] == {
        "host": "2.2.2.2",
        "api_key": "123456789",
        "api_version": 1,
    }
    entries = hass.config_entries.async_entries("hue")
    assert len(entries) == 2
    entry = entries[-1]
    assert entry.unique_id == "id-1234"


async def test_manual_flow_bridge_exist(hass):
    """Test config flow aborts on already configured bridges."""
    MockConfigEntry(
        domain="hue", unique_id="id-1234", data={"host": "2.2.2.2"}
    ).add_to_hass(hass)

    with patch(
        "homeassistant.components.hue.config_flow.discover_nupnp",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            const.DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "2.2.2.2"}
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_manual_flow_no_discovered_bridges(hass, aioclient_mock):
    """Test config flow discovers no bridges."""
    create_mock_api_discovery(aioclient_mock, [])

    result = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "manual"


async def test_flow_all_discovered_bridges_exist(hass, aioclient_mock):
    """Test config flow discovers only already configured bridges."""
    mock_host = "1.2.3.4"
    mock_id = "bla"
    create_mock_api_discovery(aioclient_mock, [(mock_host, mock_id)])

    MockConfigEntry(
        domain="hue", unique_id=mock_id, data={"host": mock_host}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "manual"


async def test_flow_bridges_discovered(hass, aioclient_mock):
    """Test config flow discovers two bridges."""
    # Add ignored config entry. Should still show up as option.
    MockConfigEntry(
        domain="hue", source=config_entries.SOURCE_IGNORE, unique_id="bla"
    ).add_to_hass(hass)

    create_mock_api_discovery(
        aioclient_mock, [("1.2.3.4", "bla"), ("5.6.7.8", "beer_v2")]
    )

    result = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    with pytest.raises(vol.Invalid):
        assert result["data_schema"]({"id": "not-discovered"})

    result["data_schema"]({"id": "bla"})
    result["data_schema"]({"id": "beer_v2"})
    result["data_schema"]({"id": "manual"})


async def test_flow_two_bridges_discovered_one_new(hass, aioclient_mock):
    """Test config flow discovers two bridges."""
    create_mock_api_discovery(aioclient_mock, [("1.2.3.4", "bla"), ("5.6.7.8", "beer")])
    MockConfigEntry(
        domain="hue", unique_id="bla", data={"host": "1.2.3.4"}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["data_schema"]({"id": "beer"})
    assert result["data_schema"]({"id": "manual"})
    with pytest.raises(vol.error.MultipleInvalid):
        assert not result["data_schema"]({"id": "bla"})


async def test_flow_timeout_discovery(hass):
    """Test config flow ."""
    with patch(
        "homeassistant.components.hue.config_flow.discover_nupnp",
        side_effect=asyncio.TimeoutError,
    ):
        result = await hass.config_entries.flow.async_init(
            const.DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    assert result["type"] == "abort"
    assert result["reason"] == "discover_timeout"


async def test_flow_link_unknown_error(hass):
    """Test if a unknown error happened during the linking processes."""
    disc_bridge = get_discovered_bridge()
    with patch(
        "homeassistant.components.hue.config_flow.discover_nupnp",
        return_value=[disc_bridge],
    ):
        result = await hass.config_entries.flow.async_init(
            const.DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    with patch.object(config_flow, "create_app_key", side_effect=Exception):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"id": disc_bridge.id}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "link"
    assert result["errors"] == {"base": "linking"}


async def test_flow_link_button_not_pressed(hass):
    """Test config flow ."""
    disc_bridge = get_discovered_bridge()
    with patch(
        "homeassistant.components.hue.config_flow.discover_nupnp",
        return_value=[disc_bridge],
    ):
        result = await hass.config_entries.flow.async_init(
            const.DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    with patch.object(config_flow, "create_app_key", side_effect=LinkButtonNotPressed):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"id": disc_bridge.id}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "link"
    assert result["errors"] == {"base": "register_failed"}


async def test_flow_link_cannot_connect(hass):
    """Test config flow ."""
    disc_bridge = get_discovered_bridge()
    with patch(
        "homeassistant.components.hue.config_flow.discover_nupnp",
        return_value=[disc_bridge],
    ):
        result = await hass.config_entries.flow.async_init(
            const.DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

    with patch.object(config_flow, "create_app_key", side_effect=CannotConnect):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"id": disc_bridge.id}
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] == "abort"
    assert result["reason"] == "cannot_connect"


@pytest.mark.parametrize("mf_url", config_flow.HUE_MANUFACTURERURL)
async def test_bridge_ssdp(hass, mf_url, aioclient_mock):
    """Test a bridge being discovered."""
    create_mock_api_discovery(aioclient_mock, [("0.0.0.0", "1234")])
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=ssdp.SsdpServiceInfo(
            ssdp_usn="mock_usn",
            ssdp_st="mock_st",
            ssdp_location="http://0.0.0.0/",
            upnp={
                ssdp.ATTR_UPNP_MANUFACTURER_URL: mf_url,
                ssdp.ATTR_UPNP_SERIAL: "1234",
            },
        ),
    )

    assert result["type"] == "form"
    assert result["step_id"] == "link"


async def test_bridge_ssdp_discover_other_bridge(hass):
    """Test that discovery ignores other bridges."""
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=ssdp.SsdpServiceInfo(
            ssdp_usn="mock_usn",
            ssdp_st="mock_st",
            upnp={ssdp.ATTR_UPNP_MANUFACTURER_URL: "http://www.notphilips.com"},
        ),
    )

    assert result["type"] == "abort"
    assert result["reason"] == "not_hue_bridge"


async def test_bridge_ssdp_emulated_hue(hass):
    """Test if discovery info is from an emulated hue instance."""
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=ssdp.SsdpServiceInfo(
            ssdp_usn="mock_usn",
            ssdp_st="mock_st",
            ssdp_location="http://0.0.0.0/",
            upnp={
                ssdp.ATTR_UPNP_FRIENDLY_NAME: "Home Assistant Bridge",
                ssdp.ATTR_UPNP_MANUFACTURER_URL: config_flow.HUE_MANUFACTURERURL[0],
                ssdp.ATTR_UPNP_SERIAL: "1234",
            },
        ),
    )

    assert result["type"] == "abort"
    assert result["reason"] == "not_hue_bridge"


async def test_bridge_ssdp_missing_location(hass):
    """Test if discovery info is missing a location attribute."""
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=ssdp.SsdpServiceInfo(
            ssdp_usn="mock_usn",
            ssdp_st="mock_st",
            upnp={
                ssdp.ATTR_UPNP_MANUFACTURER_URL: config_flow.HUE_MANUFACTURERURL[0],
                ssdp.ATTR_UPNP_SERIAL: "1234",
            },
        ),
    )

    assert result["type"] == "abort"
    assert result["reason"] == "not_hue_bridge"


async def test_bridge_ssdp_missing_serial(hass):
    """Test if discovery info is a serial attribute."""
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=ssdp.SsdpServiceInfo(
            ssdp_usn="mock_usn",
            ssdp_st="mock_st",
            ssdp_location="http://0.0.0.0/",
            upnp={
                ssdp.ATTR_UPNP_MANUFACTURER_URL: config_flow.HUE_MANUFACTURERURL[0],
            },
        ),
    )

    assert result["type"] == "abort"
    assert result["reason"] == "not_hue_bridge"


async def test_bridge_ssdp_invalid_location(hass):
    """Test if discovery info is a serial attribute."""
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=ssdp.SsdpServiceInfo(
            ssdp_usn="mock_usn",
            ssdp_st="mock_st",
            ssdp_location="http:///",
            upnp={
                ssdp.ATTR_UPNP_MANUFACTURER_URL: config_flow.HUE_MANUFACTURERURL[0],
                ssdp.ATTR_UPNP_SERIAL: "1234",
            },
        ),
    )

    assert result["type"] == "abort"
    assert result["reason"] == "not_hue_bridge"


async def test_bridge_ssdp_espalexa(hass):
    """Test if discovery info is from an Espalexa based device."""
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=ssdp.SsdpServiceInfo(
            ssdp_usn="mock_usn",
            ssdp_st="mock_st",
            ssdp_location="http://0.0.0.0/",
            upnp={
                ssdp.ATTR_UPNP_FRIENDLY_NAME: "Espalexa (0.0.0.0)",
                ssdp.ATTR_UPNP_MANUFACTURER_URL: config_flow.HUE_MANUFACTURERURL[0],
                ssdp.ATTR_UPNP_SERIAL: "1234",
            },
        ),
    )

    assert result["type"] == "abort"
    assert result["reason"] == "not_hue_bridge"


async def test_bridge_ssdp_already_configured(hass, aioclient_mock):
    """Test if a discovered bridge has already been configured."""
    create_mock_api_discovery(aioclient_mock, [("0.0.0.0", "1234")])
    MockConfigEntry(
        domain="hue", unique_id="1234", data={"host": "0.0.0.0"}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=ssdp.SsdpServiceInfo(
            ssdp_usn="mock_usn",
            ssdp_st="mock_st",
            ssdp_location="http://0.0.0.0/",
            upnp={
                ssdp.ATTR_UPNP_MANUFACTURER_URL: config_flow.HUE_MANUFACTURERURL[0],
                ssdp.ATTR_UPNP_SERIAL: "1234",
            },
        ),
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_import_with_no_config(hass, aioclient_mock):
    """Test importing a host without an existing config file."""
    create_mock_api_discovery(aioclient_mock, [("0.0.0.0", "1234")])
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
        data={"host": "0.0.0.0"},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "link"


async def test_creating_entry_removes_entries_for_same_host_or_bridge(
    hass, aioclient_mock
):
    """Test that we clean up entries for same host and bridge.

    An IP can only hold a single bridge and a single bridge can only be
    accessible via a single IP. So when we create a new entry, we'll remove
    all existing entries that either have same IP or same bridge_id.
    """
    create_mock_api_discovery(aioclient_mock, [("2.2.2.2", "id-1234")])
    orig_entry = MockConfigEntry(
        domain="hue",
        data={"host": "0.0.0.0", "api_key": "123456789"},
        unique_id="id-1234",
    )
    orig_entry.add_to_hass(hass)

    MockConfigEntry(
        domain="hue",
        data={"host": "1.2.3.4", "api_key": "123456789"},
        unique_id="id-5678",
    ).add_to_hass(hass)

    assert len(hass.config_entries.async_entries("hue")) == 2

    result = await hass.config_entries.flow.async_init(
        "hue",
        data={"host": "2.2.2.2"},
        context={"source": config_entries.SOURCE_IMPORT},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "link"

    with patch(
        "homeassistant.components.hue.config_flow.create_app_key",
        return_value="123456789",
    ), patch("homeassistant.components.hue.async_unload_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == "create_entry"
    assert result["title"] == "Hue Bridge id-1234"
    assert result["data"] == {
        "host": "2.2.2.2",
        "api_key": "123456789",
        "api_version": 1,
    }
    entries = hass.config_entries.async_entries("hue")
    assert len(entries) == 2
    new_entry = entries[-1]
    assert orig_entry.entry_id != new_entry.entry_id
    assert new_entry.unique_id == "id-1234"


async def test_bridge_homekit(hass, aioclient_mock):
    """Test a bridge being discovered via HomeKit."""
    create_mock_api_discovery(aioclient_mock, [("0.0.0.0", "bla")])

    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_HOMEKIT},
        data=zeroconf.ZeroconfServiceInfo(
            host="0.0.0.0",
            hostname="mock_hostname",
            name="mock_name",
            port=None,
            properties={zeroconf.ATTR_PROPERTIES_ID: "aa:bb:cc:dd:ee:ff"},
            type="mock_type",
        ),
    )

    assert result["type"] == "form"
    assert result["step_id"] == "link"

    flow = next(
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["flow_id"] == result["flow_id"]
    )
    assert flow["context"]["unique_id"] == config_entries.DEFAULT_DISCOVERY_UNIQUE_ID


async def test_bridge_import_already_configured(hass):
    """Test if a import flow aborts if host is already configured."""
    MockConfigEntry(
        domain="hue", unique_id="aabbccddeeff", data={"host": "0.0.0.0"}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
        data={"host": "0.0.0.0", "properties": {"id": "aa:bb:cc:dd:ee:ff"}},
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_bridge_homekit_already_configured(hass, aioclient_mock):
    """Test if a HomeKit discovered bridge has already been configured."""
    create_mock_api_discovery(aioclient_mock, [("0.0.0.0", "aabbccddeeff")])
    MockConfigEntry(
        domain="hue", unique_id="aabbccddeeff", data={"host": "0.0.0.0"}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_HOMEKIT},
        data=zeroconf.ZeroconfServiceInfo(
            host="0.0.0.0",
            hostname="mock_hostname",
            name="mock_name",
            port=None,
            properties={zeroconf.ATTR_PROPERTIES_ID: "aa:bb:cc:dd:ee:ff"},
            type="mock_type",
        ),
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_ssdp_discovery_update_configuration(hass, aioclient_mock):
    """Test if a discovered bridge is configured and updated with new host."""
    create_mock_api_discovery(aioclient_mock, [("1.1.1.1", "aabbccddeeff")])
    entry = MockConfigEntry(
        domain="hue", unique_id="aabbccddeeff", data={"host": "0.0.0.0"}
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_SSDP},
        data=ssdp.SsdpServiceInfo(
            ssdp_usn="mock_usn",
            ssdp_st="mock_st",
            ssdp_location="http://1.1.1.1/",
            upnp={
                ssdp.ATTR_UPNP_MANUFACTURER_URL: config_flow.HUE_MANUFACTURERURL[0],
                ssdp.ATTR_UPNP_SERIAL: "aabbccddeeff",
            },
        ),
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == "1.1.1.1"


async def test_options_flow_v1(hass):
    """Test options config flow for a V1 bridge."""
    entry = MockConfigEntry(
        domain="hue",
        unique_id="aabbccddeeff",
        data={"host": "0.0.0.0"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    schema = result["data_schema"].schema
    assert (
        _get_schema_default(schema, const.CONF_ALLOW_HUE_GROUPS)
        == const.DEFAULT_ALLOW_HUE_GROUPS
    )
    assert (
        _get_schema_default(schema, const.CONF_ALLOW_UNREACHABLE)
        == const.DEFAULT_ALLOW_UNREACHABLE
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            const.CONF_ALLOW_HUE_GROUPS: True,
            const.CONF_ALLOW_UNREACHABLE: True,
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        const.CONF_ALLOW_HUE_GROUPS: True,
        const.CONF_ALLOW_UNREACHABLE: True,
    }


def _get_schema_default(schema, key_name):
    """Iterate schema to find a key."""
    for schema_key in schema:
        if schema_key == key_name:
            return schema_key.default()
    raise KeyError(f"{key_name} not found in schema")


async def test_options_flow_v2(hass):
    """Test options config flow for a V2 bridge."""
    entry = MockConfigEntry(
        domain="hue",
        unique_id="v2bridge",
        data={"host": "0.0.0.0", "api_version": 2},
    )
    entry.add_to_hass(hass)

    assert config_flow.HueFlowHandler.async_supports_options_flow(entry) is False


async def test_bridge_zeroconf(hass, aioclient_mock):
    """Test a bridge being discovered."""
    create_mock_api_discovery(aioclient_mock, [("192.168.1.217", "ecb5fafffeabcabc")])
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf.ZeroconfServiceInfo(
            host="192.168.1.217",
            port=443,
            hostname="Philips-hue.local",
            type="_hue._tcp.local.",
            name="Philips Hue - ABCABC._hue._tcp.local.",
            properties={
                "_raw": {"bridgeid": b"ecb5fafffeabcabc", "modelid": b"BSB002"},
                "bridgeid": "ecb5fafffeabcabc",
                "modelid": "BSB002",
            },
        ),
    )

    assert result["type"] == "form"
    assert result["step_id"] == "link"


async def test_bridge_zeroconf_already_exists(hass, aioclient_mock):
    """Test a bridge being discovered by zeroconf already exists."""
    create_mock_api_discovery(
        aioclient_mock, [("0.0.0.0", "ecb5faabcabc"), ("192.168.1.217", "ecb5faabcabc")]
    )
    entry = MockConfigEntry(
        domain="hue",
        source=config_entries.SOURCE_SSDP,
        data={"host": "0.0.0.0"},
        unique_id="ecb5faabcabc",
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf.ZeroconfServiceInfo(
            host="192.168.1.217",
            port=443,
            hostname="Philips-hue.local",
            type="_hue._tcp.local.",
            name="Philips Hue - ABCABC._hue._tcp.local.",
            properties={
                "_raw": {"bridgeid": b"ecb5faabcabc", "modelid": b"BSB002"},
                "bridgeid": "ecb5faabcabc",
                "modelid": "BSB002",
            },
        ),
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == "192.168.1.217"
