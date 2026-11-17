"""Bounded, process-local operational counters; never store message text or identities."""
from collections import Counter, deque
from threading import Lock
import math
import time


class Metrics:
    def __init__(self):
        self.started = time.time()
        self.lock = Lock()
        self.series = {}
        self.caches = {}

    def observe(self, group, name, duration_ms, outcome):
        with self.lock:
            key = (group, name)
            row = self.series.setdefault(key, {'count': 0, 'outcomes': Counter(), 'durations': deque(maxlen=1000), 'total_ms': 0})
            row['count'] += 1
            row['outcomes'][outcome] += 1
            row['durations'].append(duration_ms)
            row['total_ms'] += duration_ms

    def cache(self, kind, hit):
        if kind not in {'llm', 'stt', 'tts', 'web-search', 'web_page', 'rag_query', 'retrieval_query'}:
            return
        with self.lock:
            row = self.caches.setdefault(kind, {'hits': 0, 'misses': 0})
            row['hits' if hit else 'misses'] += 1

    def snapshot(self):
        with self.lock:
            rows = []
            for (group, name), row in sorted(self.series.items()):
                samples = sorted(row['durations'])
                rows.append({'group': group, 'name': name, 'count': row['count'], 'outcomes': dict(row['outcomes']), 'mean_ms': round(row['total_ms']/row['count'], 1), 'p95_ms': round(samples[max(0, math.ceil(len(samples)*.95)-1)],1), 'sample_count': len(samples)})
            return {'since': self.started, 'series': rows, 'caches': {key: dict(value) for key,value in self.caches.items()}, 'p95_window': 1000}
