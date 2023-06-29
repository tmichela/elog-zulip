from functools import partial, wraps
from time import sleep


def retry(func=None, *, attempts=1, delay=0, exc=(Exception,)):
    """Re-execute decorated function.

    :attemps int: number of tries, default 1
    :delay float: timeout between each tries in seconds, default 0
    :exc tuple: collection of exceptions to be caugth
    """
    if func is None:
        return partial(retry, attempts=attempts, delay=delay, exc=exc)

    @wraps(func)
    def retried(*args, **kwargs):
        retry._tries[func.__name__] = 0
        for i in reversed(range(attempts)):
            retry._tries[func.__name__] += 1
            try:
                ret = func(*args, *kwargs)
            except exc:
                if i <= 0:
                    raise
                sleep(delay)
                continue
            else:
                break
        return ret

    retry._tries = {}
    return retried
