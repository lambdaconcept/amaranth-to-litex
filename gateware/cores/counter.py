from amaranth import *

from ..interface import stream


# https://github.com/amaranth-lang/amaranth/blob/main/examples/basic/ctr_en.py
class Counter(Elaboratable):
    def __init__(self, width):
        self.v = Signal(width, reset=2**width-1)
        self.o = Signal()
        self.en = Signal()

    def elaborate(self, platform):
        m = Module()
        m.d.sync += self.v.eq(self.v + 1)
        m.d.comb += self.o.eq(self.v[-1])
        return EnableInserter(self.en)(m)


class CounterStream(Elaboratable):
    def __init__(self, width):
        self.width = width
        self.source = stream.Endpoint([("data", width)])

    def elaborate(self, platform):
        source = self.source

        m = Module()

        m.submodules.cnt = cnt = Counter(self.width)

        m.d.comb += [
            source.data.eq(cnt.v),
            source.valid.eq(1),
            source.first.eq(cnt.v == 0),
            source.last.eq(cnt.v == 2**self.width-1),

            cnt.en.eq(self.source.ready),
        ]

        return m


from amaranth.sim import *


def test():
    dut = CounterStream(8)
    sim = Simulator(dut)

    def bench():
        yield dut.source.ready.eq(1)
        for i in range(1000):
            yield

    sim.add_clock(1e-6)
    sim.add_sync_process(bench)
    with sim.write_vcd("tests/cores/counter.vcd"):
        sim.run()


if __name__ == "__main__":
    test()
