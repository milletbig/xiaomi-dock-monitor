import unittest

from dockmon.pnp import UsbDevice, CLASS_GUID


def make_device(class_guid: str, name: str = "device") -> UsbDevice:
    return UsbDevice(
        instance_id=r"USB\VID_0BDA&PID_8153\001000001",
        vid=0x0BDA,
        pid=0x8153,
        name=name,
        friendly_name=name,
        driver_key=r"{4d36e972-e325-11ce-bfc1-08002be10318}\0014",
        class_guid=class_guid,
        service="rtux64w10",
        location="Port_#0002.Hub_#0005",
    )


class DeviceClassTests(unittest.TestCase):
    def test_network_class(self):
        device = make_device(CLASS_GUID["net"], "Realtek USB GbE")
        self.assertTrue(device.is_network)
        self.assertEqual(device.class_label, "网络")
        self.assertEqual(device.vid_pid, "0BDA:8153")

    def test_storage_class(self):
        self.assertTrue(make_device(CLASS_GUID["disk"]).is_storage)

    def test_audio_class(self):
        self.assertTrue(make_device(CLASS_GUID["media"]).is_audio)
        self.assertTrue(make_device(CLASS_GUID["audio_endpoint"]).is_audio)

    def test_hid_class(self):
        self.assertTrue(make_device(CLASS_GUID["hid"]).is_hid)

    def test_unknown_class(self):
        device = make_device("{00000000-0000-0000-0000-000000000000}")
        self.assertEqual(device.class_kind, "usb")
        self.assertFalse(device.is_network)


if __name__ == "__main__":
    unittest.main()
