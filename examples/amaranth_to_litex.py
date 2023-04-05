import os

from migen import *

from litex_boards.platforms import lambdaconcept_ecpix5

import sys
sys.path.append(".")

from gateware.cores.counter import Counter as AmaranthCounter
from gateware.cores.counter import CounterStream as AmaranthCounterStream
from gateware.wrapper.litex import amaranth_to_litex


class Top(Module):
    def __init__(self, platform):

        self.submodules.cnt = amaranth_to_litex(platform,
            AmaranthCounterStream(width=26),
        )

        self.comb += self.cnt.source.ready.eq(1)

        led = platform.request("rgb_led", 0)
        self.comb += [
            led.r.eq(self.cnt.source.data[-1]),
            led.g.eq(self.cnt.source.data[-1]),
            led.b.eq(self.cnt.source.data[-1]),
        ]


def main():
    platform = lambdaconcept_ecpix5.Platform(device="85F")

    top = Top(platform)

    platform.build(top)


if __name__ == "__main__":
    main()
