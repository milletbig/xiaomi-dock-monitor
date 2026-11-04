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
    def test_usb_views_fill_slots_and_count_extra(self):
        from dockmon.dock import _usb_views

        hub = HubInfo("h", "hub", 0x2717, 0x50BA, "sym", True)
        ports = [
            PortInfo(i, True, 1, "已连接", 0x1200 + i, 0x3400 + i, "2.1", "480 Mbps", 480, False, f"key{i}")
            for i in range(1, 7)
        ]
        external = [(hub, port, None) for port in ports]
        views, extra = _usb_views(external)
        self.assertEqual([view.id for view in views], ["usb_a1", "usb_a2", "usb_a3", "usb_c"])
        self.assertTrue(all(view.status == STATUS_CONNECTED for view in views))
        self.assertEqual(extra, 2)

    def test_empty_usb_slots_are_idle(self):
        from dockmon.dock import _usb_views

        views, extra = _usb_views([])
        self.assertEqual(len(views), 4)
        self.assertTrue(all(view.status == STATUS_IDLE for view in views))
        self.assertEqual(extra, 0)


class PortViewTests(unittest.TestCase):
    def test_defaults(self):
        view = PortView("lan", "RJ45", "lan", STATUS_IDLE, "空闲")
        self.assertEqual(view.speed, "—")
        self.assertEqual(view.power, "—")


if __name__ == "__main__":
    unittest.main()
