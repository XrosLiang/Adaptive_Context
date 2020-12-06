from matplotlib import pyplot as plt

import matplotlib
import os
import random
import torch
from torch.autograd import Variable
import torchvision.transforms as standard_transforms
import misc.transforms as own_transforms
import pandas as pd

from models.CC import CrowdCounter
from config import cfg
from misc.utils import *
import scipy.io as sio
from PIL import Image, ImageOps
import sys

#import quailitycc

torch.cuda.set_device(0)
torch.backends.cudnn.benchmark = True

mean_std = ([0.446139603853, 0.409515678883, 0.395083993673], [0.288205742836, 0.278144598007, 0.283502370119])
img_transform = standard_transforms.Compose([
        standard_transforms.ToTensor(),
        standard_transforms.Normalize(*mean_std)
    ])
restore = standard_transforms.Compose([
        own_transforms.DeNormalize(*mean_std),
        standard_transforms.ToPILImage()
    ])
pil_to_tensor = standard_transforms.ToTensor()
LOG_PARA = 100.0


dataRoot = '/home/deeplp/mainspace/NWPUData/min_576x768_mod16_2048'
#model_path = '/deeplp/mainspace/git_dir/NWPU-Crowd-Sample-Code/exp/02-19_22-56_NWPU_SCAR_1e-05/all_ep_103_mae_85.0_mse_274.6_nae_0.450.pth'
model_path = '/deeplp/mainspace/git_dir/NWPU-Crowd-Sample-Code/exp/03-18_18-04_NWPU_SCAR_1e-05/all_ep_152_mae_83.9_mse_216.5_nae_0.781.pth'

def main():
    txtpath = os.path.join(dataRoot, 'txt_list', 'test.txt')
    with open(txtpath) as f:
        lines = f.readlines()                            
    test(lines, model_path)
   

def test(file_list, model_path):

    net = CrowdCounter(cfg.GPU_ID, 'SCAR')
    net.cuda()
    net.load_state_dict(torch.load(model_path))
    net.eval()

    gts = []
    preds = []
    f = open(f'submmitedscar+mycontext.txt', 'w+')

    for infos in file_list:
        #print('infors',infos)
        #filename, illum, level = infos.split()
        filename = infos.replace('\n', '').replace('\r', '').strip()
        imgname = os.path.join(dataRoot, 'img', filename + '.jpg')
        img = Image.open(imgname)
        if img.mode == 'L':
            img = img.convert('RGB')
        img = img_transform(img)[None, :, :, :]
        with torch.no_grad():
            img = Variable(img).cuda()
            crop_imgs, crop_masks = [], []
            b, c, h, w = img.shape
            rh, rw = 576, 768
            for i in range(0, h, rh):
                gis, gie = max(min(h-rh, i), 0), min(h, i+rh)
                for j in range(0, w, rw):
                    gjs, gje = max(min(w-rw, j), 0), min(w, j+rw)
                    crop_imgs.append(img[:, :, gis:gie, gjs:gje])
                    mask = torch.zeros(b, 1, h, w).cuda()
                    mask[:, :, gis:gie, gjs:gje].fill_(1.0)
                    crop_masks.append(mask)
            crop_imgs, crop_masks = map(lambda x: torch.cat(x, dim=0), (crop_imgs, crop_masks))

            # forward may need repeatng
            crop_preds = []
            nz, bz = crop_imgs.size(0), 1
            for i in range(0, nz, bz):
                gs, gt = i, min(nz, i+bz)
                crop_pred = net.test_forward(crop_imgs[gs:gt])
                crop_preds.append(crop_pred)
            crop_preds = torch.cat(crop_preds, dim=0)

            # splice them to the original size
            idx = 0
            pred_map = torch.zeros(b, 1, h, w).cuda()
            for i in range(0, h, rh):
                gis, gie = max(min(h-rh, i), 0), min(h, i+rh)
                for j in range(0, w, rw):
                    gjs, gje = max(min(w-rw, j), 0), min(w, j+rw)
                    pred_map[:, :, gis:gie, gjs:gje] += crop_preds[idx]
                    idx += 1

            # for the overlapping area, compute average value
            mask = crop_masks.sum(dim=0).unsqueeze(0)
            pred_map = pred_map / mask
        pred_map = pred_map.cpu().data.numpy()[0,0,:,:]

        pred = np.sum(pred_map) / LOG_PARA

        print(f'{filename} {pred:.4f}', file=f)
        print(f'{filename} {pred:.4f}')
    f.close()
            
if __name__ == '__main__':
    main()