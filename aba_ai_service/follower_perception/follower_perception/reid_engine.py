import cv2
import numpy as np


class ReIDEngine:
    """Appearance embedding. Tries OSNet, then torchvision MobileNetV3,
    then a 6-float colour-stats fallback. All outputs are L2-normalized."""

    def __init__(self, device=None, backend='auto'):
        self._backend = None
        self._model = None
        self._device = None
        self._tf = None
        self.feat_dim = None
        if backend == 'colour':
            self._init_colour()
        else:
            self._init_auto(device)

    def _init_colour(self):
        self._backend = 'colour'
        self.feat_dim = 6

    def _init_auto(self, device):
        try:
            import torch
            from torchreid.utils import FeatureExtractor
            dev = device or ('cuda' if torch.cuda.is_available() else 'cpu')
            self._model = FeatureExtractor(model_name='osnet_x0_25',
                                           model_path='', device=str(dev))
            self._backend = 'osnet'
            self.feat_dim = 512
            return
        except Exception:
            pass
        try:
            import torch
            import torchvision
            from torchvision import transforms
            dev = device or ('cuda' if torch.cuda.is_available() else 'cpu')
            weights = torchvision.models.MobileNet_V3_Small_Weights.DEFAULT
            model = torchvision.models.mobilenet_v3_small(weights=weights)
            model.classifier = torch.nn.Identity()
            model.eval().to(dev)
            self._model = model
            self._device = dev
            self._tf = transforms.Compose([
                transforms.ToPILImage(),
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406],
                                     [0.229, 0.224, 0.225]),
            ])
            self._backend = 'mobilenet'
            self.feat_dim = 576
            return
        except Exception:
            self._init_colour()

    def extract(self, roi_bgr) -> np.ndarray:
        if self._backend == 'colour':
            vec = self._colour_stats(roi_bgr)
        elif self._backend == 'osnet':
            rgb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB)
            vec = np.asarray(self._model(rgb).cpu().numpy()).flatten()
        else:  # mobilenet
            import torch
            rgb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB)
            tensor = self._tf(rgb).unsqueeze(0).to(self._device)
            with torch.no_grad():
                vec = self._model(tensor).cpu().numpy().flatten()
        return self._normalize(vec)

    @staticmethod
    def _colour_stats(roi_bgr) -> np.ndarray:
        flat = roi_bgr.reshape(-1, 3).astype(np.float32)
        return np.concatenate([flat.mean(axis=0), flat.std(axis=0)]).astype(np.float32)

    @staticmethod
    def _normalize(vec) -> np.ndarray:
        vec = np.asarray(vec, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec

    def similarity(self, a, b) -> float:
        a = np.asarray(a, dtype=np.float32)
        b = np.asarray(b, dtype=np.float32)
        return float(np.dot(a, b))
