import logging

from amaranth import *
from amaranth.hdl.rec import *
from amaranth.lib import fifo


__all__ = ["Endpoint", "SyncFIFO", "AsyncFIFO"]


def _make_fanout(layout):
    r = []
    for f in layout:
        if isinstance(f[1], (Shape, int, tuple, range)):
            r.append((f[0], f[1], DIR_FANOUT))
        else:
            r.append((f[0], _make_fanout(f[1])))
    return r


class EndpointDescription:
    def __init__(self, payload_layout):
        self.payload_layout = payload_layout

    def get_full_layout(self):
        reserved = {"valid", "ready", "first", "last", "payload"}
        attributed = set()
        for f in self.payload_layout:
            if f[0] in attributed:
                raise ValueError(f[0] + " already attributed in payload layout")
            if f[0] in reserved:
                raise ValueError(f[0] + " cannot be used in endpoint layout")
            attributed.add(f[0])

        full_layout = [
            ("valid", 1, DIR_FANOUT),
            ("ready", 1, DIR_FANIN),
            ("first", 1, DIR_FANOUT),
            ("last",  1, DIR_FANOUT),
            ("payload", _make_fanout(self.payload_layout))
        ]
        return full_layout


class Endpoint(Record):
    def __init__(self, layout_or_description, **kwargs):
        if isinstance(layout_or_description, EndpointDescription):
            self.description = layout_or_description
        else:
            self.description = EndpointDescription(layout_or_description)
        super().__init__(self.description.get_full_layout(), src_loc_at=1, **kwargs)

    def __getattr__(self, name):
        try:
            return super().__getattr__(name)
        except AttributeError:
            return self.fields["payload"][name]

#     def connect(self, other, exclude=None):
#         stmts = [
#             other.valid.eq(self.valid),
#             other.first.eq(self.first),
#             other.last.eq(self.last),
#             self.ready.eq(other.ready),
#         ]
#
#         if exclude is None:
#             stmts.append(self.payload.connect(other.payload))
#         else:
#             for f in self.description.payload_layout:
#                 if not f[0] in exclude:
#                     stmts.append(getattr(other, f[0]).eq(getattr(self, f[0])))
#
#         return stmts


class _FIFOWrapper:
    def __init__(self, payload_layout):
        self.sink   = Endpoint(payload_layout)
        self.source = Endpoint(payload_layout)

        self.layout = Layout([
            ("payload", self.sink.description.payload_layout),
            ("first",   1, DIR_FANOUT),
            ("last",    1, DIR_FANOUT)
        ])

    def elaborate(self, platform):
        m = Module()

        fifo = m.submodules.fifo = self.fifo
        fifo_din = Record(self.layout)
        fifo_dout = Record(self.layout)
        m.d.comb += [
            fifo.w_data.eq(fifo_din),
            fifo_dout.eq(fifo.r_data),

            self.sink.ready.eq(fifo.w_rdy),
            fifo.w_en.eq(self.sink.valid),
            fifo_din.first.eq(self.sink.first),
            fifo_din.last.eq(self.sink.last),
            fifo_din.payload.eq(self.sink.payload),

            self.source.valid.eq(fifo.r_rdy),
            self.source.first.eq(fifo_dout.first),
            self.source.last.eq(fifo_dout.last),
            self.source.payload.eq(fifo_dout.payload),
            fifo.r_en.eq(self.source.ready)
        ]

        return m


class SyncFIFO(Elaboratable, _FIFOWrapper):
    def __init__(self, layout, depth, buffered=False):
        if depth < 8:
            logging.error("SyncFIFO depth < 8 causes random bugs... forcing to 8")
            depth = 8

        super().__init__(layout)
        fifo_class = fifo.SyncFIFOBuffered if buffered else fifo.SyncFIFO
        self.fifo  = fifo_class(width=len(Record(self.layout)), depth=depth)
        self.depth = self.fifo.depth
        self.level = self.fifo.level


class AsyncFIFO(Elaboratable, _FIFOWrapper):
    def __init__(self, layout, depth, buffered=False,
                 r_domain="read", w_domain="write"):
        if depth < 8:
            logging.error("AsyncFIFO depth < 8 causes random bugs... forcing to 8")
            depth = 8

        super().__init__(layout)
        fifo_class   = fifo.AsyncFIFOBuffered if buffered else fifo.AsyncFIFO
        self.fifo    = fifo_class(width=len(Record(self.layout)), depth=depth,
                                      r_domain=r_domain, w_domain=w_domain)
        self.depth   = self.fifo.depth
        self.r_rst   = self.fifo.r_rst
        self.r_level = self.fifo.r_level


# https://github.com/enjoy-digital/litex/blob/master/litex/soc/interconnect/stream.py
class PipeValid(Elaboratable):
    """Pipe valid/payload to cut timing path"""
    def __init__(self, layout):
        self.sink   = Endpoint(layout)
        self.source = Endpoint(layout)

    def elaborate(self, platform):
        sink = self.sink
        source = self.source

        m = Module()

        en = Signal()
        m.d.comb += en.eq(~source.valid | source.ready)

        # Pipe when source is not valid or is ready.
        with m.If(en):
            m.d.sync += [
                source.valid.eq(sink.valid),
                source.first.eq(sink.first),
                source.last.eq(sink.last),
                source.payload.eq(sink.payload),
            ]
            m.d.comb += sink.ready.eq(1)

        return m


# https://github.com/enjoy-digital/litex/blob/master/litex/soc/interconnect/stream.py
class PipeReady(Elaboratable):
    """Pipe ready to cut timing path"""
    def __init__(self, layout):
        self.sink   = Endpoint(layout)
        self.source = Endpoint(layout)
        self.layout = layout

    def elaborate(self, platform):
        sink = self.sink
        source = self.source

        m = Module()

        valid  = Signal()
        sink_d = Endpoint(self.layout)

        with m.If(sink.valid & ~source.ready):
            m.d.sync += valid.eq(1)
        with m.Elif(source.ready):
            m.d.sync += valid.eq(0)

        with m.If(~source.ready & ~valid):
            m.d.sync += sink_d.eq(sink)

        m.d.comb += sink.ready.eq(~valid)

        with m.If(valid):
            m.d.comb += sink_d.connect(source, exclude={"ready"})
        with m.Else():
            m.d.comb += sink.connect(source, exclude={"ready"})

        return m


# https://github.com/enjoy-digital/litex/blob/master/litex/soc/interconnect/stream.py
class _UpConverter(Elaboratable):
    def __init__(self, nbits_from, nbits_to, ratio, reverse):
        self.nbits_from = nbits_from
        self.ratio = ratio
        self.reverse = reverse

        self.sink = sink = Endpoint([("data", nbits_from)])
        self.source = source = Endpoint([("data", nbits_to)])

    def elaborate(self, platform):
        m = Module()

        sink = self.sink
        source = self.source

        # control path
        demux = Signal(range(self.ratio))
        load_part = Signal()
        strobe_all = Signal()
        m.d.comb += [
            sink.ready.eq(~strobe_all | source.ready),
            source.valid.eq(strobe_all),
            load_part.eq(sink.valid & sink.ready)
        ]

        demux_last = ((demux == (self.ratio - 1)) | sink.last)

        with m.If(source.ready):
            m.d.sync += strobe_all.eq(0)
        with m.If(load_part):
            with m.If(demux_last):
                m.d.sync += [
                    demux.eq(0),
                    strobe_all.eq(1)
                ]
            with m.Else():
                m.d.sync += demux.eq(demux + 1)

        with m.If(source.valid & source.ready):
            with m.If(sink.valid & sink.ready):
                m.d.sync += [
                    source.first.eq(sink.first),
                    source.last.eq(sink.last)
                ]
            with m.Else():
                m.d.sync += [
                    source.first.eq(0),
                    source.last.eq(0)
                ]
        with m.Elif(sink.valid & sink.ready):
            m.d.sync += [
                source.first.eq(sink.first | source.first),
                source.last.eq(sink.last | source.last)
            ]

        # data path
        with m.Switch(demux):
            for i in range(self.ratio):
                n = self.ratio-i-1 if self.reverse else i
                with m.Case(i):
                    with m.If(load_part):
                        m.d.sync += source.data[n*self.nbits_from:(n+1)*self.nbits_from].eq(sink.data)

        return m


# https://github.com/enjoy-digital/litex/blob/master/litex/soc/interconnect/stream.py
class _DownConverter(Elaboratable):
    def __init__(self, nbits_from, nbits_to, ratio, reverse):
        self.nbits_to = nbits_to
        self.ratio = ratio
        self.reverse = reverse

        self.sink = sink = Endpoint([("data", nbits_from)])
        self.source = source = Endpoint([("data", nbits_to)])

    def elaborate(self, platform):
        m = Module()

        sink = self.sink
        source = self.source

        # control path
        mux = Signal(range(self.ratio))
        first = Signal()
        last = Signal()
        m.d.comb += [
            first.eq(mux == 0),
            last.eq(mux == (self.ratio-1)),
            source.valid.eq(sink.valid),
            source.first.eq(sink.first & first),
            source.last.eq(sink.last & last),
            sink.ready.eq(last & source.ready)
        ]

        with m.If(source.valid & source.ready):
            with m.If(last):
                m.d.sync += mux.eq(0)
            with m.Else():
                m.d.sync += mux.eq(mux + 1)

        # data path
        with m.Switch(mux):
            for i in range(self.ratio):
                n = self.ratio-i-1 if self.reverse else i
                with m.Case(i):
                    m.d.comb += source.data.eq(sink.data[n*self.nbits_to:(n+1)*self.nbits_to])

        return m


# https://github.com/enjoy-digital/litex/blob/master/litex/soc/interconnect/stream.py
class _IdentityConverter(Elaboratable):
    def __init__(self, nbits_from, nbits_to, ratio, reverse):
        self.sink = sink = Endpoint([("data", nbits_from)])
        self.source = source = Endpoint([("data", nbits_to)])

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.sink.connect(self.source)

        return m


# https://github.com/enjoy-digital/litex/blob/master/litex/soc/interconnect/stream.py
def _get_converter_ratio(nbits_from, nbits_to):
    if nbits_from > nbits_to:
        converter_cls = _DownConverter
        if nbits_from % nbits_to:
            raise ValueError("Ratio must be an int")
        ratio = nbits_from//nbits_to
    elif nbits_from < nbits_to:
        converter_cls = _UpConverter
        if nbits_to % nbits_from:
            raise ValueError("Ratio must be an int")
        ratio = nbits_to//nbits_from
    else:
        converter_cls = _IdentityConverter
        ratio = 1

    return converter_cls, ratio


# https://github.com/enjoy-digital/litex/blob/master/litex/soc/interconnect/stream.py
class Converter(Elaboratable):
    def __init__(self, nbits_from, nbits_to, reverse=False):
        cls, ratio = _get_converter_ratio(nbits_from, nbits_to)
        self.converter = cls(nbits_from, nbits_to, ratio, reverse)
        self.sink = self.converter.sink
        self.source = self.converter.source

    def elaborate(self, platform):
        return self.converter
