"""Tests du verrou de calcul NER / OCR (``api.compute``)."""

import threading
import time

from api.compute import ComputeLock


def test_shared_holders_run_together():
    lock = ComputeLock()
    inside, peak = 0, 0
    guard = threading.Lock()

    def ocr():
        nonlocal inside, peak
        with lock.shared():
            with guard:
                inside += 1
                peak = max(peak, inside)
            time.sleep(0.05)
            with guard:
                inside -= 1

    threads = [threading.Thread(target=ocr) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak > 1


def test_exclusive_never_overlaps_shared():
    lock = ComputeLock()
    log: list[str] = []
    guard = threading.Lock()

    def ocr():
        for _ in range(20):
            with lock.shared():
                with guard:
                    log.append("ocr+")
                time.sleep(0.001)
                with guard:
                    log.append("ocr-")

    def ner():
        for _ in range(10):
            with lock.exclusive():
                with guard:
                    log.append("ner+")
                time.sleep(0.002)
                with guard:
                    log.append("ner-")

    threads = [threading.Thread(target=ocr) for _ in range(3)] + [threading.Thread(target=ner)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "interblocage"

    active_ocr, in_ner = 0, False
    for entry in log:
        if entry == "ner+":
            assert active_ocr == 0 and not in_ner
            in_ner = True
        elif entry == "ner-":
            in_ner = False
        elif entry == "ocr+":
            assert not in_ner
            active_ocr += 1
        else:
            active_ocr -= 1


def test_waiting_exclusive_blocks_new_shared():
    """Une analyse en attente passe avant les OCR suivants (pas de famine)."""
    lock = ComputeLock()
    order: list[str] = []
    first_ocr_in = threading.Event()
    release_first = threading.Event()

    def first_ocr():
        with lock.shared():
            first_ocr_in.set()
            release_first.wait(5)
        order.append("ocr1-")

    def ner():
        with lock.exclusive():
            order.append("ner")

    def late_ocr():
        with lock.shared():
            order.append("ocr2")

    t1 = threading.Thread(target=first_ocr)
    t1.start()
    first_ocr_in.wait(5)
    t2 = threading.Thread(target=ner)
    t2.start()
    time.sleep(0.05)  # l'analyse attend désormais
    t3 = threading.Thread(target=late_ocr)
    t3.start()
    time.sleep(0.05)
    release_first.set()
    for t in (t1, t2, t3):
        t.join(5)
    assert order.index("ner") < order.index("ocr2")
