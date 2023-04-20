# 2023 - LambdaConcept - po@lambdaconcept.com

from migen import *

from luna.gateware.architecture.car import PHYResetController

from lambdalib.cores.usb import USBGenericDevice
from counter import CounterStream

from amaranth_to_litex import *


class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):

        clk100 = platform.request("clk100")

        # System clock
        self.clock_domains += ClockDomain("sys")
        self.comb += ClockSignal("sys").eq(clk100)

        # USB clock
        self.clock_domains += ClockDomain("usb")
        platform.add_period_constraint(ClockSignal("usb"), 1e9/60e6)

        # USB reset controller (LUNA)
        self.submodules.usb_ctrl = usb_ctrl = amaranth_to_litex(platform,
            PHYResetController(),
        )
        self.comb += ResetSignal("usb").eq(usb_ctrl.phy_reset)


class Top(Module):
    def __init__(self, sys_clk_freq, platform):

        # CRG
        self.submodules.crg = _CRG(
            platform, sys_clk_freq,
        )

        # Convert Litex pads to Amaranth pins
        litex_pads = platform.request("ulpi")
        # Amaranth platforms specify the pin direction "i", "o", "io",
        # but not Litex platforms. Some Amaranth modules rely on this
        # for accessing pins by their subsignals (pin.i, pin.oe, ...)
        # In such case for correct mapping we need to help
        # the converter by passing an explicit direction hint.
        usb_pins = amaranth_pins_from_litex(litex_pads, {
            "data": "io",
            "clk" : "i",
            "dir" : "i",
        })

        # Create the USB device (LUNA)
        self.submodules.dev = dev = amaranth_to_litex(platform,
            USBGenericDevice(
                pins=usb_pins,
                vid=0xffff, pid=0x1234,
            ),
        )

        # Create a dummy data generator
        self.submodules.cnt = cnt = amaranth_to_litex(platform,
            CounterStream(width=8),
        )

        # Send dummy data to the USB IN endpoint
        self.comb += cnt.source.connect(dev.sink)


def main():
    from litex_boards.platforms import lambdaconcept_ecpix5

    platform = lambdaconcept_ecpix5.Platform(device="85F")
    top = Top(100e6, platform)
    platform.build(top, build_dir="build.usb")


if __name__ == "__main__":
    main()
