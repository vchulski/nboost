from resources.es.shakespeare import ensure_shakespeare_elasticsearch
from nboost.cli import create_proxy
from statistics import mean
import requests
import time


def benchmark_es_proxy(ext_host='localhost', argv=[], laps=50):
    ensure_shakespeare_elasticsearch()
    query = 'shakespeare/_search?q=text_entry:palace'
    proxy = create_proxy(argv + ['--ext_host', ext_host])
    proxy.start()

    times = [[], []]
    for i in range(laps):
        _1 = time.perf_counter()
        requests.get('http://localhost:9000/%s' % query)
        _2 = time.perf_counter()
        requests.get('http://%s:9200/%s' % (ext_host, query))
        _3 = time.perf_counter()
        times[0].append(_2-_1)
        times[1].append(_3-_2)

    t1, t2 = mean(times[0]) * 10 ** 3, mean(times[1]) * 10 ** 3
    print(requests.get('http://localhost:9000/status').text)
    print()
    print('Proxy avg: %s ms; Server avg %s ms' % (t1, t2))
    proxy.close()
    print('DONE')


if __name__ == '__main__':
    benchmark_es_proxy(argv=[
        '--codex', 'ESCodex',
        '--model', 'TestModel',
        '--port', '9000',
        '--ext_port', '9200',
        '--field', 'text_entry',
        '--multiplier', '2',
        '--verbose'
    ], ext_host='35.238.60.182', laps=100)