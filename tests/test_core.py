import unittest

from dockmon.dock import PortView, STATUS_CONNECTED, STATUS_IDLE
from dockmon.metrics import format_bytes_rate, format_link_speed
from dockmon.usbhub import PortInfo, HubInfo, _parse_vid_pid, _speed_from_flags


class FormattingTests(unittest.TestCase):
    def test_format_bytes_rate(self):
        self.assertEqual(format_bytes_rate(0), "0 B/s")
        self.assertEqual(format_bytes_rate(1024), "1.0 KB/s")
        self.assertEqual(format_bytes_rate(1024 * 1024), "1.0 MB/s")
        self.assertEqual(format_bytes_rate(1024 ** 3), "1.0 GB/s")

    def test_format_link_speed(self):
        self.assertEqual(format_link_speed(0), "未连接")
        self.assertEqual(format_link_speed(1000), "1 Gbps")
        self.assertEqual(format_link_speed(2500), "2.5 Gbps")
        self.assertEqual(format_link_speed(100), "100 Mbps")


class UsbParsingTests(unittest.TestCase):
    def test_parse_vid_pid(self):
        self.assertEqual(_parse_vid_pid(r"USB\VID_2717&PID_50BA\6&24176d9b&0&5"), (0x2717, 0x50BA))
        self.assertEqual(_parse_vid_pid("no ids"), (0, 0))

    def test_speed_from_flags(self):
        self.assertEqual(_speed_from_flags(2, 0), ("480 Mbps", 480))
        self.assertEqual(_speed_from_flags(1, 0), ("12 Mbps", 12))
        self.assertEqual(_speed_from_flags(0, 0), ("1.5 Mbps", 1))
        self.assertEqual(_speed_from_flags(2, 0x1), ("5 Gbps", 5000))
        self.assertEqual(_speed_from_flags(2, 0x5), ("10 Gbps+", 10000))


class PortAssignmentTests(unittest.TestCase):
    def _hub(self, instance: str, description: str, pid: int) -> HubInfo:
        return HubInfo(instance, description, 0x2717, pid, "sym", True)

    def _port(self, number: int, vid: int, pid: int, usb_version: str, speed: str) -> PortInfo:
        return PortInfo(
            number, True, 1, "已连接", vid, pid, usb_version, speed,
            {"5 Gbps": 5000, "480 Mbps": 480, "12 Mbps": 12}.get(speed, 0), False, f"key{number}",
        )

    def test_measured_ports_map_to_physical_numbers(self):
        from dockmon.dock import _usb_views

        ss_hub = self._hub(
            r"USB\VID_2717&PID_50BA\6&24176d9b&0&5", "Generic SuperSpeed USB Hub", 0x50BA
        )
        usb2_hub = self._hub(
            r"USB\VID_2717&PID_50BB\6&24176d9b&0&1", "Generic USB Hub", 0x50BB
        )
        hdd = self._port(2, 0x152D, 0x0583, "3.0", "5 Gbps")
        watch = self._port(4, 0x05AC, 0x0503, "2.0", "12 Mbps")

        views, unmapped = _usb_views([(ss_hub, hdd, None), (usb2_hub, watch, None)])

        self.assertEqual([view.id for view in views], ["usb_c", "usb_a1", "usb_a2", "usb_a3"])
        self.assertEqual([view.number for view in views], [2, 3, 4, 5])
        self.assertEqual(views[0].label, "2 · Type-C 数据")
        self.assertEqual(views[1].label, "3 · USB-A 1")
        self.assertEqual(views[0].detail.split(" · ")[0], "05AC:0503")
        self.assertEqual(views[1].detail.split(" · ")[0], "152D:0583")
        self.assertEqual(views[2].status, STATUS_IDLE)
        self.assertEqual(unmapped, [])

    def test_unmapped_top_level_port_is_reported(self):
        from dockmon.dock import _usb_views

        ss_hub = self._hub(
            r"USB\VID_2717&PID_50BA\6&24176d9b&0&5", "Generic SuperSpeed USB Hub", 0x50BA
        )
        device = self._port(3, 0x1A2B, 0x3C4D, "2.1", "480 Mbps")

        views, unmapped = _usb_views([(ss_hub, device, None)])

        self.assertEqual(len(unmapped), 1)
        self.assertEqual(unmapped[0].hub_role, "SS")
        self.assertEqual(unmapped[0].hub_port, 3)
        self.assertEqual(unmapped[0].vid_pid, "1A2B:3C4D")
        self.assertTrue(all(view.status == STATUS_IDLE for view in views))

    def test_nested_hub_port_is_not_a_physical_port(self):
        from dockmon.dock import _usb_views

        nested = self._hub(
            r"USB\VID_2717&PID_50BA\7&2ae5b31f&0&1", "Generic SuperSpeed USB Hub", 0x50BA
        )
        device = self._port(3, 0x1A2B, 0x3C4D, "2.1", "480 Mbps")

        views, unmapped = _usb_views([(nested, device, None)])

        self.assertEqual(unmapped, [])
        self.assertTrue(all(view.status == STATUS_IDLE for view in views))

    def test_empty_usb_slots_are_idle(self):
        from dockmon.dock import _usb_views

        views, unmapped = _usb_views([])
        self.assertEqual([view.number for view in views], [2, 3, 4, 5])
        self.assertTrue(all(view.status == STATUS_IDLE for view in views))
        self.assertEqual(unmapped, [])


class PortViewTests(unittest.TestCase):
    def test_defaults(self):
        view = PortView("lan", "RJ45", "lan", STATUS_IDLE, "空闲")
        self.assertEqual(view.speed, "—")
        self.assertEqual(view.power, "—")


if __name__ == "__main__":
    unittest.main()
