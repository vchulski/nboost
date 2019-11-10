from typing import Type, Tuple, Dict, Any
from inspect import isawaitable
from ..base import StatefulBase
from ..server import BaseServer
from ..codex import BaseCodex
from ..model import BaseModel
from ..base.types import *
from ..db import BaseDb
import time


class Proxy(StatefulBase):
    def __init__(self,
                 server: Type[BaseServer] = BaseServer,
                 model: Type[BaseModel] = BaseModel,
                 codex: Type[BaseCodex] = BaseCodex,
                 db: Type[BaseDb] = BaseDb,
                 **kwargs):
        """The proxy object is the core of nboost.It has four components:
        the model, server, db, and codex.The role of the proxy is to
        construct each component and create the route callback(search,
        train, status, not_found, and error).

        Each route callback is assigned a url path by the codex and then
        handed to the server to execute when it receives the respective url
        path request.The following __init__ contains the main executed
        functions in nboost.

        :param host: virtual host of the server.
        :param port: server port.
        :param ext_host: host of the external search api.
        :param ext_port: search api port.
        :param lr: learning rate of the model.
        :param model_ckpt: checkpoint for loading initial model
        :param data_dir: data directory to cache the model.
        :param multiplier: the factor to multiply the search request by. For
            example, in the case of Elasticsearch if the client requests 10
            results and the multiplier is 6, then the model should receive 60
            results to rank and refine down to 10 (better) results.
        :param field: a tag for the field in the search api result that the
            model should rank results by.
        :param server: uninitialized server class
        :param model: uninitialized model class
        :param codex: uninitialized codex class
        :param db: uninitialized db class
        """

        super().__init__(**kwargs)

        # pass command line arguments to instantiate each component
        server = server(**kwargs)
        model = model(**kwargs)
        codex = codex(**kwargs)
        db = db(**kwargs)

        def track(f: Any):
            """Tags and times each component for benchmarking purposes. The
            data generated by track() is sent to the db who decides what to do
            with it (e.g. log, add to /status, etc...)"""

            if hasattr(f, '__self__'):
                cls = f.__self__.__class__.__name__
            else:
                cls = self.__class__.__name__
            # ident is the name of the object containing f() or the Proxy
            ident = (cls, f.__name__)

            async def decorator(*args):
                try:
                    start = time.perf_counter()
                    res = f(*args)
                    ret = await res if isawaitable(res) else res
                    ms = (time.perf_counter() - start) * 1000
                    db.lap(ms, *ident)
                except Exception as e:
                    self.logger.error(repr(e), exc_info=True)
                    ret = codex.catch(e)
                return ret
            return decorator

        @track
        async def search(_1: Request) -> Response:
            """The role of the search route is to take a request from the
            client, balloon it by the multipler and ask for that larger request
            from the search api, then filter the larger results with the
            model to return better results. """

            # Codex figures out how many results the client wants.
            _2: Topk = await track(codex.topk)(_1)

            # Codex alters the request to make a larger one.
            _3: Request = await track(codex.magnify)(_1, _2)

            # The server asks the search api for the larger request.
            _4: Response = await track(server.ask)(_3)

            # The codex takes the large response and parses out the query
            # from the amplified request and response.
            _5: Tuple[Query, Choices] = await track(codex.parse)(_3, _4)

            # the model ranks the choices based on the query.
            _6: Ranks = await track(model.rank)(*_5)

            # the db saves the query and choices, and returns the query id
            # and choice ids for the client to send back during train()
            _7: Tuple[Qid, List[Cid]] = await track(db.save)(*_5)

            # the codex formats the new (nboosted) response with the context
            # from the entire search pipeline.
            _8: Response = await track(codex.pack)(_2, _4, *_5, _6, *_7)
            return _8

        @track
        async def train(_1: Request) -> Response:
            """The role of the train route is to receive a query id and choice
            id from the client and train the model to choose that one next
            time for lack of better words."""

            # Parse out the query id and choice id(s) from the client request.
            _2: Tuple[Qid, List[Cid]] = await track(codex.pluck)(_1)

            # Db retrieves the content it saved during search(). It also
            # assigns a label to each choice based on the clients request.
            _3: Tuple[Query, Choices, Labels] = await track(db.get)(*_2)
            await track(model.train)(*_3)

            # acknowledge that the request was sent to the model
            _4: Response = await track(codex.ack)(*_2)
            return _4

        @track
        async def status(_1: Request) -> Response:
            """Status() chains the state from each component in order to
            return a formatted dictionary for /status"""
            _2: Dict = server.chain_state({})
            _3: Dict = codex.chain_state(_2)
            _4: Dict = model.chain_state(_3)
            _5: Dict = db.chain_state(_4)
            _6: Response = codex.pulse(_5)
            return _6

        @track
        async def not_found(_1: Any) -> Any:
            """What to do when none of the paths given to the server match
            the path requested by the client."""
            _2: Any = await track(server.forward)(_1)
            return _2

        # create functional routes for the server
        server.create_app([
            (codex.SEARCH, search),
            (codex.TRAIN, train),
            (codex.STATUS, status)
        ], not_found_handler=not_found)

        self.server = server

    def start(self):
        self.logger.critical('STARTING SERVER')
        self.server.start()
        self.server.is_ready.wait()

    def close(self):
        self.server.stop()
        self.server.join()
