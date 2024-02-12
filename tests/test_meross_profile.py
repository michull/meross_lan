"""Test for meross cloud profiles"""

from datetime import timedelta
from unittest import mock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import flush_store

from custom_components.meross_lan import MerossApi, const as mlc
from custom_components.meross_lan.meross_profile import MerossCloudProfile
from custom_components.meross_lan.merossclient import HostAddress, cloudapi, const as mc

from . import const as tc, helpers


async def test_cloudapi(hass, cloudapi_mock: helpers.CloudApiMocker):
    cloudapiclient = cloudapi.CloudApiClient(session=async_get_clientsession(hass))
    credentials = await cloudapiclient.async_signin(
        tc.MOCK_PROFILE_EMAIL, tc.MOCK_PROFILE_PASSWORD
    )
    assert credentials == tc.MOCK_PROFILE_CREDENTIALS_SIGNIN

    result = await cloudapiclient.async_device_devlist()
    assert result == tc.MOCK_CLOUDAPI_DEVICE_DEVLIST

    result = await cloudapiclient.async_device_latestversion()
    assert result == tc.MOCK_CLOUDAPI_DEVICE_LATESTVERSION

    result = await cloudapiclient.async_hub_getsubdevices(tc.MOCK_PROFILE_MSH300_UUID)
    assert result == tc.MOCK_CLOUDAPI_HUB_GETSUBDEVICES[tc.MOCK_PROFILE_MSH300_UUID]

    await cloudapiclient.async_logout()


async def test_meross_profile(
    hass: HomeAssistant,
    hass_storage,
    aioclient_mock,
    cloudapi_mock: helpers.CloudApiMocker,
    merossmqtt_mock: helpers.MerossMQTTMocker,
    time_mock: helpers.TimeMocker,
):
    """
    Tests basic MerossCloudProfile (alone) behavior:
    - loading
    - starting (with cloud device_info list update)
    - discovery setup
    - saving
    """
    hass_storage.update(tc.MOCK_PROFILE_STORAGE)
    async with helpers.ProfileEntryMocker(hass) as profile_entry_mock:
        assert (profile := MerossApi.profiles.get(tc.MOCK_PROFILE_ID))
        # check we have refreshed our device list
        # the device discovery starts when we setup the entry and it might take
        # some while since we're queueing multiple requests (2)
        # so we'll

        await time_mock.async_tick(5)
        await time_mock.async_tick(5)
        await hass.async_block_till_done()

        assert len(cloudapi_mock.api_calls) >= 2
        assert cloudapi_mock.api_calls[cloudapi.API_DEVICE_DEVLIST_PATH] == 1
        assert cloudapi_mock.api_calls[cloudapi.API_DEVICE_LATESTVERSION_PATH] == 1
        # check the cloud profile connected the mqtt server(s)
        # for discovery of devices. Our truth comes from
        # the cloudapi recovered device list
        expected_connections = set()
        for device_info in tc.MOCK_CLOUDAPI_DEVICE_DEVLIST:
            expected_connections.add(device_info[mc.KEY_DOMAIN])
            expected_connections.add(device_info[mc.KEY_RESERVEDDOMAIN])
        # check our profile built the expected number of connections
        mqttconnections = list(profile.mqttconnections.values())
        assert len(mqttconnections) == len(expected_connections)
        # and activated them (not less/no more)
        safe_start_calls = []
        for expected_connection in expected_connections:
            broker = HostAddress.build(expected_connection)
            connection_id = f"{broker.host}:{broker.port}"
            mqttconnection = profile.mqttconnections[connection_id]
            mqttconnections.remove(mqttconnection)
            safe_start_calls.append(mock.call(mqttconnection, broker))
        assert len(mqttconnections) == 0
        merossmqtt_mock.safe_start_mock.assert_has_calls(
            safe_start_calls,
            any_order=True,
        )
        await flush_store(profile._store)
        # check the store has been persisted with cloudapi fresh device list
        profile_storage_data = hass_storage[tc.MOCK_PROFILE_STORE_KEY]["data"]
        expected_storage_device_info_data = {
            device_info[mc.KEY_UUID]: device_info
            for device_info in tc.MOCK_CLOUDAPI_DEVICE_DEVLIST
        }
        assert (
            profile_storage_data[MerossCloudProfile.KEY_DEVICE_INFO]
            == expected_storage_device_info_data
        )
        # check the update firmware versions was stored
        assert (
            profile_storage_data[MerossCloudProfile.KEY_LATEST_VERSION]
            == tc.MOCK_CLOUDAPI_DEVICE_LATESTVERSION
        )

        # check cleanup
        assert await profile_entry_mock.async_unload()
        assert MerossApi.profiles[tc.MOCK_PROFILE_ID] is None
        assert merossmqtt_mock.safe_stop_mock.call_count == len(safe_start_calls)


async def test_meross_profile_cloudapi_offline(
    hass: HomeAssistant,
    hass_storage,
    aioclient_mock,
    cloudapi_mock: helpers.CloudApiMocker,
    merossmqtt_mock: helpers.MerossMQTTMocker,
    time_mock: helpers.TimeMocker,
):
    """
    Tests basic MerossCloudProfile (alone) behavior:
    - loading
    - starting (with cloud api offline)
    - discovery setup
    """
    cloudapi_mock.online = False
    hass_storage.update(tc.MOCK_PROFILE_STORAGE)
    async with helpers.ProfileEntryMocker(hass) as profile_entry_mock:
        assert (profile := MerossApi.profiles.get(tc.MOCK_PROFILE_ID))
        time_mock.tick(mlc.PARAM_CLOUDPROFILE_DELAYED_SETUP_TIMEOUT)
        time_mock.tick(mlc.PARAM_CLOUDPROFILE_DELAYED_SETUP_TIMEOUT)
        await hass.async_block_till_done()

        # check we have tried to refresh our device list
        assert len(cloudapi_mock.api_calls) == 1
        assert cloudapi_mock.api_calls[cloudapi.API_DEVICE_DEVLIST_PATH] == 1
        # check the cloud profile connected the mqtt server(s)
        # for discovery of devices. Since the device list was not refreshed
        # we check against our stored list of devices
        expected_connections = set()
        """
        # update 2023-12-08: on entry setup we're not automatically querying
        # the stored device list
        for device_info in tc.MOCK_PROFILE_STORE_DEVICEINFO_DICT.values():
            expected_connections.add(device_info[mc.KEY_DOMAIN])
            expected_connections.add(device_info[mc.KEY_RESERVEDDOMAIN])
        """
        # check our profile built the expected number of connections
        mqttconnections = list(profile.mqttconnections.values())
        assert len(mqttconnections) == len(expected_connections)
        # and activated them (not less/no more)
        safe_start_calls = []
        for expected_connection in expected_connections:
            broker = HostAddress.build(expected_connection)
            connection_id = f"{tc.MOCK_PROFILE_ID}:{broker.host}:{broker.port}"
            mqttconnection = profile.mqttconnections[connection_id]
            mqttconnections.remove(mqttconnection)
            safe_start_calls.append(mock.call(mqttconnection, broker))
        assert len(mqttconnections) == 0
        merossmqtt_mock.safe_start_mock.assert_has_calls(
            safe_start_calls,
            any_order=True,
        )
        # check cleanup
        assert await profile_entry_mock.async_unload()
        assert MerossApi.profiles[tc.MOCK_PROFILE_ID] is None
        assert merossmqtt_mock.safe_stop_mock.call_count == len(safe_start_calls)


async def test_meross_profile_with_device(
    hass: HomeAssistant,
    hass_storage,
    aioclient_mock,
    cloudapi_mock: helpers.CloudApiMocker,
    merossmqtt_mock: helpers.MerossMQTTMocker,
):
    """
    Tests basic MerossCloudProfile behavior:
    - loading
    - starting (with cloud device_info list update)
    - discovery setup
    - saving
    """
    hass_storage.update(tc.MOCK_PROFILE_STORAGE)

    async with (
        helpers.DeviceContext(
            hass,
            helpers.build_emulator_for_profile(
                tc.MOCK_PROFILE_CONFIG, model=mc.TYPE_MSS310
            ),
            aioclient_mock,
            config_data={
                mlc.CONF_PROTOCOL: mlc.CONF_PROTOCOL_AUTO,
            },
        ) as devicecontext,
        helpers.ProfileEntryMocker(hass, auto_setup=False) as profile_entry_mock,
    ):
        # the loading order of the config entries might
        # have side-effects because of device<->profile binding
        # beware: we cannot selectively load config_entries here
        # since component initialization load them all
        assert await devicecontext.async_setup()

        assert (api := devicecontext.api)
        assert (device := devicecontext.device)
        assert (profile := api.profiles.get(tc.MOCK_PROFILE_ID))

        assert device._profile is profile
        assert device._mqtt_connection in profile.mqttconnections.values()
        assert device._mqtt_connected is device._mqtt_connection

        device = await devicecontext.perform_coldstart()

        assert device.update_firmware is None

        # check the device registry has the device name from the cloud (stored)
        assert (
            device_registry_entry := device_registry.async_get(hass).async_get_device(
                **device.deviceentry_id
            )
        ) and device_registry_entry.name == tc.MOCK_PROFILE_MSS310_DEVNAME_STORED
        # now the profile should query the cloudapi and get an updated device_info list
        await devicecontext.async_tick(
            timedelta(seconds=mlc.PARAM_CLOUDPROFILE_DELAYED_SETUP_TIMEOUT)
        )
        mqttconnections = list(profile.mqttconnections.values())
        merossmqtt_mock.safe_start_mock.assert_has_calls(
            [
                mock.call(
                    mqttconnections[0], HostAddress(tc.MOCK_PROFILE_MSS310_DOMAIN, 443)
                ),
                mock.call(
                    mqttconnections[1], HostAddress(tc.MOCK_PROFILE_MSH300_DOMAIN, 443)
                ),
            ],
            any_order=True,
        )
        # check the device name was update from cloudapi query
        assert (
            device_registry_entry := device_registry.async_get(hass).async_get_device(
                **device.deviceentry_id
            )
        ) and device_registry_entry.name == tc.MOCK_PROFILE_MSS310_DEVNAME
        assert cloudapi_mock.api_calls[cloudapi.API_DEVICE_DEVLIST_PATH] == 1

        # remove the cloud profile
        assert await profile_entry_mock.async_unload()
        assert api.profiles[tc.MOCK_PROFILE_ID] is None
        assert device._profile is None
        assert device._mqtt_connection is None
        assert device._mqtt_connected is None
