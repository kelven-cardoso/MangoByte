import json
import copy
import logging
import asyncio
import aiohttp
import logging.handlers
from multiprocessing import Queue
from typing import OrderedDict, List, Dict, Tuple, TypedDict, Optional

from utils.tools.settings import settings

MANGO_LOGGER_NAME = "mangologger"

# Se quiser registrar disnake material aqui https://docs.disnake.dev/en/latest/logging.html?highlight=logger
# também podemos obter o root logger, que nos dará uma tonelada de informações para todas as bibliotecas que temos

trace_level = 21
logging.addLevelName(trace_level, "TRACE")
# este nível de log captura eventos json que acontecem durante o mangobyte
def trace(self, message, *args, **kws):
	if self.isEnabledFor(trace_level):
		message = json.dumps(message)
		self._log(trace_level, message, args, **kws)
logging.Logger.trace = trace

def event(self, type, data = {}):
	if self.isEnabledFor(trace_level):
		data = OrderedDict(data)
		data["type"] = type
		data.move_to_end("type", last=False)
		message = json.dumps(data)
		self._log(trace_level, message, [])
logging.Logger.event = event

# cria um objeto de evento, mas no nível "info", para que seja excluído após 30 dias
def event_info(self, type, data = {}):
	if self.isEnabledFor(logging.INFO):
		data = OrderedDict(data)
		data["type"] = type
		data.move_to_end("type", last=False)
		message = json.dumps(data)
		self._log(logging.INFO, message, [])
logging.Logger.event_info = event_info

def setup_logger():
	logger = logging.getLogger(MANGO_LOGGER_NAME)

	if settings.debug:
		logger.setLevel(logging.DEBUG)
	else:
		logger.setLevel(logging.INFO)
		
	return logger


# LOKI LOGGING STUFF
# aqui foi fortemente inspirado por https://github.com/AXVin/aioloki

class LokiStream(TypedDict):
	stream: Dict[str, str]
	values: List[Tuple[str, str]]

class LokiPayload(TypedDict):
	streams: List[LokiStream]

class AioLokiHandler(logging.Handler):
	def __init__(
		self,
		url: str,
		/, *,
		session: aiohttp.ClientSession,
		tags: Optional[Dict[str, str]]=None,
	) -> None:
		super().__init__()
		self._queue: asyncio.Queue[logging.LogRecord] = asyncio.Queue()
		self.url = url + '/loki/api/v1/push'
		self.session = session
		self.tags = tags
		self._task = asyncio.create_task(self._queue_worker())

	async def _queue_worker(self):
		try:
			while True:
				log = await self._queue.get()
				payload = self.build_payload(log)
				try:
					async with self.session.post(self.url, json=payload) as response:
						if response.status != 204:
							print("Loki logger bad response: ", response.status)
							print("Sleeping 1 min before retrying")
							self._queue.put_nowait(log)
							await asyncio.sleep(60)
				except asyncio.TimeoutError: # if we get a timeout error, dont break our loop. re-add the log to the queue and keep goin.
					print("Timeout on loki logging. Re-trying...")
					self._queue.put_nowait(log)
		except asyncio.CancelledError as e:
			print("LOKI LOGGER CANCELLED: ", e)
		except Exception as e:
			print("LOKI LOGGER BROKE: ", e)

	def build_tags(self, log: logging.LogRecord, /):
		tags = copy.deepcopy(self.tags) or {}
		tags["level"] = log.levelname.lower()
		tags["logger"] = log.name
		try:
			extra_tags = log.tags # type: ignore
		except AttributeError:
			pass
		else:
			tags.update(extra_tags)
		return tags

	def build_payload(self, log: logging.LogRecord, /) -> LokiPayload:
		labels = self.build_tags(log)
		return {
			"streams": [{
				"stream": labels,
				"values": [
					(str(int(log.created * 1e9)), self.format(log))
				]
			}]
		}

	def emit(self, record: logging.LogRecord) -> None:
		self._queue.put_nowait(record)

# chame isso para inicializar o logger assim que o loop for criado
async def init_logger():
	loki_config = settings.loki
	logger = logging.getLogger(MANGO_LOGGER_NAME)
	
	if settings.debug or loki_config is None:
		consoleout = logging.StreamHandler()
		logger.addHandler(consoleout)

	if loki_config is None:
		return None

	baseurl = loki_config["base_url"]

	loop = asyncio.get_event_loop()
	session = aiohttp.ClientSession(loop=loop, auth=aiohttp.BasicAuth(loki_config["username"], loki_config["password"]))
	handler = AioLokiHandler(
		baseurl,
		tags={"application": loki_config["application"]},
		session=session
	)
	logger.addHandler(handler)

logger = setup_logger()
