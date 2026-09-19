"""ML worker for app/animals.py - runs in .mlvenv, never inside the web process.

Protocol: JSON lines. We print {"type":"ready",...} once the models are loaded, then
for every {"id","path","frame_out"} on stdin one {"type":"result"|"error",...} line.

Engine: DeepFaune 1.5.0 (CNRS) - YOLOv8s detector @960 with MegaDetector-sorrel as a
backstop (animal / person / vehicle), then a DINOv3 ViT-L classifier on the animal crop
(European fauna: chat, renard, mustelide, herisson, blaireau...).

Sampling: animals usually trigger the recording and may leave within seconds, so the
first seconds are sampled densely (4 fps for 12 s) and the rest at 2 fps.
CLI test:  .mlvenv/bin/python ml/worker.py clip.mp4 [more.mp4 ...]
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "deepfaune"))
_real_stdout = sys.stdout
sys.stdout = sys.stderr                      # libraries print freely; the protocol stays clean

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

W, H = 1920, 1080                            # decode size: plenty for a 960 px detector + crops
DENSE_SECONDS, DENSE_FPS, SPARSE_FPS = 12.0, 4.0, 2.0
CLASSIF_MIN = 0.50                           # below: keep the detection as plain "animal"
ANIMAL_CONFIRM = 0.80                        # a single hit needs this; else 2 hits are required
FR2KEY = {"chat": "cat", "renard": "fox", "mustelide": "mustelid", "blaireau": "badger",
          "herisson": "hedgehog", "chien": "dog", "oiseau": "bird", "ecureuil": "squirrel",
          "chevreuil": "roe_deer", "cerf": "deer", "daim": "deer", "sanglier": "wild_boar",
          "lagomorphe": "lagomorph", "micromammifere": "rodent", "loutre": "mustelid"}


PLAUSIBLE = {"cat", "fox", "mustelid", "badger", "hedgehog", "dog", "bird", "squirrel", "roe_deer",
             "deer", "wild_boar", "lagomorph", "rodent", "animal", "person", "vehicle"}


def _drop_static(hits, n_frames):
    """Remove boxes that sit at the same place in most frames (a dark corner, a statue,
    a parked car): real visitors move. A motionless but confidently classified animal
    (the cat sitting on the wall, median score >= 0.9) is kept."""
    flat = [(k, h) for k, hs in hits.items() for h in hs]
    clusters = []                            # [cx, cy, size, members]
    for k, h in flat:
        x1, y1, x2, y2 = h[2]
        cx, cy, sz = (x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1, 1)
        for c in clusters:
            if abs(cx - c[0]) < 0.03 * W and abs(cy - c[1]) < 0.03 * W and 0.7 < sz / c[2] < 1.4:
                c[3].append((k, h))
                break
        else:
            clusters.append([cx, cy, sz, [(k, h)]])
    out = {}
    for c in clusters:
        members = c[3]
        static = len(members) >= 6           # same spot in 6+ frames = not moving (clusters are 3 % wide)
        if static:
            scores = sorted(h[1] for k, h in members if k not in ("person", "vehicle", "animal"))
            confident = len(scores) >= 0.6 * len(members) and scores[len(scores) // 2] >= 0.9
            if not confident:
                continue
        for k, h in members:
            out.setdefault(k, []).append(h)
    return out


def send(msg):
    _real_stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    _real_stdout.flush()


def frames(path):
    """Yield (t_seconds, BGR frame): dense at the start of the clip, sparser after."""
    expr = f"if(lt(t\\,{DENSE_SECONDS})\\,{1 / DENSE_FPS}\\,{1 / SPARSE_FPS})"
    vf = (f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{expr})',"
          f"scale={W}:{H},showinfo")
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "info", "-threads", "4", "-i", path, "-an",
           "-vf", vf, "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    times = []

    def read_times():                        # showinfo prints pts_time of each selected frame
        for line in proc.stderr:
            i = line.find(b"pts_time:")
            if i >= 0 and b"showinfo" in line:
                try:
                    times.append(float(line[i + 9:].split()[0]))
                except ValueError:
                    pass
    import threading
    threading.Thread(target=read_times, daemon=True).start()
    size, n = W * H * 3, 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            for _ in range(50):              # the matching showinfo line arrives around the frame
                if len(times) > n:
                    break
                time.sleep(0.01)
            t = times[n] if len(times) > n else n / SPARSE_FPS
            n += 1
            yield t, np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    finally:
        proc.kill()
        proc.wait()


class Engine:
    def __init__(self):
        from classifTools import Classifier, txt_animalclasses
        from detectTools import Detector
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        torch.set_num_threads(4)
        self.detector = Detector("DFbsMDS", device=self.device)
        self.classifier = Classifier(device=self.device) if "device" in Classifier.__init__.__code__.co_varnames \
            else Classifier()
        self.classes = txt_animalclasses["fr"]
        self.model = "deepfaune-1.5.0"

    def analyze(self, path, frame_out=None):
        t0 = time.time()
        hits = {}                            # key -> list of (t, score, box)
        best = None                          # (score, frame, box, key)
        n = 0
        for t, frame in frames(path):
            n += 1
            crop, category, box, _count, humans = self.detector.bestBoxDetection(frame)
            if category == 1 and crop is not None:
                with torch.no_grad():
                    scores = self.classifier.predictOnBatch(self.classifier.preprocessImage(crop))[0][0]
                k = int(np.argmax(scores))
                score = float(scores[k])
                fr = self.classes[k]
                key = FR2KEY.get(fr, fr) if score >= CLASSIF_MIN else "animal"
                if key not in PLAUSIBLE:     # vache, genette, bison... in a Belgian garden: no
                    key, score = "animal", 0.5
                hits.setdefault(key, []).append((t, score if key != "animal" else 0.5, box))
                if key != "animal" and (best is None or score > best[0]):
                    best = (score, frame.copy(), box, key)
                elif best is None:
                    best = (0.0, frame.copy(), box, key)
            elif category == 2 or len(humans):
                hits.setdefault("person", []).append((t, 0.9, box))
            elif category == 3:
                hits.setdefault("vehicle", []).append((t, 0.9, box))
        hits = _drop_static(hits, n)
        dets = []
        for key, hs in hits.items():
            top = max(h[1] for h in hs)
            if key in ("person", "vehicle"):
                if len(hs) < 3:              # lantern / house shapes fire once in a while
                    continue
            elif len(hs) < 2 or (key != "animal" and top < ANIMAL_CONFIRM) or (key == "animal" and len(hs) < 3):
                continue                     # one-frame "species" are almost always noise
            dets.append({"label": key, "score": round(top, 3), "hits": len(hs),
                         "first": round(min(h[0] for h in hs), 1), "last": round(max(h[0] for h in hs), 1)})
        species = [d for d in dets if d["label"] not in ("person", "vehicle", "animal")]
        if species:                          # side labels riding on the main animal's track are noise
            main = max(species, key=lambda d: d["hits"])
            dets = [d for d in dets if d not in species or d is main
                    or d["hits"] >= max(5, 0.25 * main["hits"])]
        dets = [d for d in dets if d["label"] != "animal" or (d["hits"] >= 8 and not species)]
        order = {"person": 1, "vehicle": 2}
        dets.sort(key=lambda d: (order.get(d["label"], 0), -d["score"] * min(d["hits"], 5)))
        if frame_out and best is not None and any(d["label"] == best[3] for d in dets):
            img, (x1, y1, x2, y2) = best[1], [int(v) for v in best[2]]
            cv2.rectangle(img, (x1, y1), (x2, y2), (60, 220, 60), 3)
            cv2.imwrite(frame_out, cv2.resize(img, (960, 540)), [cv2.IMWRITE_JPEG_QUALITY, 85])
        elif frame_out and os.path.exists(frame_out):
            os.remove(frame_out)
        return {"type": "result", "model": self.model, "device": self.device, "frames": n,
                "seconds": round(time.time() - t0, 1), "detections": dets}


def main():
    try:
        eng = Engine()
    except Exception as e:  # noqa: BLE001
        send({"type": "error", "error": f"chargement des modèles impossible : {e}"})
        return 1
    if len(sys.argv) > 1:                    # CLI test mode
        for p in sys.argv[1:]:
            r = eng.analyze(p, os.path.splitext(p)[0] + "_animal.jpg" if os.environ.get("SAVE") else None)
            print(os.path.basename(p), r["frames"], "frames", r["seconds"], "s ->",
                  [(d["label"], d["score"], d["hits"], d["first"]) for d in r["detections"]], file=_real_stdout)
        return 0
    send({"type": "ready", "model": eng.model, "device": eng.device})
    for line in sys.stdin:
        try:
            req = json.loads(line)
            res = eng.analyze(req["path"], req.get("frame_out"))
            res["id"] = req.get("id")
            send(res)
        except Exception as e:  # noqa: BLE001
            send({"type": "error", "id": None, "error": str(e)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
