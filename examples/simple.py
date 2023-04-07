from migen import *

from amaranth_to_litex import *
from counter import CounterStream


class Top(Module):
    def __init__(self, platform):

        self.submodules.cnt = amaranth_to_litex(platform,
            CounterStream(width=26),
        )

        self.comb += self.cnt.source.ready.eq(1)

        led = platform.request("rgb_led", 0)
        self.comb += [
            led.r.eq(self.cnt.source.data[-1]),
            led.g.eq(self.cnt.source.data[-1]),
            led.b.eq(self.cnt.source.data[-1]),
        ]


def main():
    from litex_boards.platforms import lambdaconcept_ecpix5

    platform = lambdaconcept_ecpix5.Platform(device="85F")
    top = Top(platform)
    platform.build(top)


if __name__ == "__main__":
    main()
