import time
import random
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)

class ChaosConfig:
    def __init__(self, latency_ms: float = 0, jitter_ms: float = 0, drop_probability: float = 0.0, crash_probability: float = 0.0, seed: int = None):
        self.latency_ms = latency_ms
        self.jitter_ms = jitter_ms
        self.drop_probability = drop_probability
        self.crash_probability = crash_probability
        
        # Seed for reproducible chaos
        if seed is not None:
            random.seed(seed)

class ChaosInjector:
    """
    A wrapper class that injects controlled chaos (latency, drops, crashes) 
    into function calls (simulating network or I/O boundaries).
    """
    def __init__(self, config: ChaosConfig):
        self.config = config

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """
        Executes a function through the chaos filter.
        """
        # 1. Evaluate Crash
        if self.config.crash_probability > 0:
            if random.random() < self.config.crash_probability:
                logger.critical("CHAOS: Simulated Process Crash (SIGKILL)!")
                raise SystemExit("Simulated Chaos Crash")

        # 2. Evaluate Packet/Message Drop
        if self.config.drop_probability > 0:
            if random.random() < self.config.drop_probability:
                logger.warning("CHAOS: Message Dropped")
                raise ConnectionAbortedError("Simulated Network Drop")

        # 3. Evaluate Latency & Jitter
        total_delay_sec = 0.0
        if self.config.latency_ms > 0:
            total_delay_sec += (self.config.latency_ms / 1000.0)
            
        if self.config.jitter_ms > 0:
            # Random jitter between 0 and jitter_ms
            jitter_sec = (random.random() * self.config.jitter_ms) / 1000.0
            total_delay_sec += jitter_sec
            
        if total_delay_sec > 0:
            logger.debug(f"CHAOS: Injecting {total_delay_sec*1000:.2f}ms latency")
            time.sleep(total_delay_sec)

        # 4. Execute actual function
        return func(*args, **kwargs)

# Simulated Network Client
class MockFixClient:
    def send_order(self, order_id: str) -> str:
        return f"ACK-{order_id}"
