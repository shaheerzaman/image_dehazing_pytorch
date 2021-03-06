import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import os
from pathlib import Path
import mimetypes
from glob import glob
import matplotlib.pyplot as plt
import numpy as np
import itertools
import logging
from os.path import splitext
from os import listdir
from torch.utils.data import Dataset
from torchvision.models import resnet34
from torchvision.transforms import Compose
os.environ['TORCH_HOME'] = '/root/home/'

path = '/root/home/data/'
path_hr = path + '/normal'
path_lr = path + '/hazy'

def plot_img_mask(img, mask):
    classes = mask.shape[2] if len(mask.shape) > 2 else 1
    fig, ax = plt.subplots(1, classes+1)
    ax[0].set_title('Input image')
    ax[0].imshow(img)
    if classes > 1:
        for i in range(classes):
            ax[i+1].set_title(f'output mask (class {i+1})')
            ax[i+1].imshow(mask[:, :, i])
    else:
        ax[1].set_title(f'output mask')
        ax[1].imshow(mask)
    plt.xticks([]), plt.yticks([])
    plt.show()

class BasicDataste(Dataset):
    def __init__(self, imgs_dir, masks_dir, scale=1):
        self.imgs_dir = imgs_dir
        self.masks_dir = masks_dir
        self.scale = scale
        assert 0 < scale <= 1, 'Scale must be between 0 and 1'
        
        self.ids = [splitext(file)[0] for file in listdir(imgs_dir) if file not file.startswith('.')]
        
    def __len__(self):
        return len(self.ids)

    @staticmethod
    def preprocess(pil_img, scale):
        w, h = pil_img.size
        newW, newH = int(scale*w), int(scale*h)
        assert newW > 0 and newH > 0,  'scale  is too small'
        pil_img = pil_img.resize((newW, newH))
        
        img_nd = np.array(pil_img)
        
        if len(img_nd.shape) == 2:
            img_nd = np.expand_dims(img_nd, axis=2)
            
        img_trans = np.transpose(img_nd, (2, 0, 1))
        if np.max(img_trans) > 1:
            img_trans /= 255
        
        return img_trans

    def __getitem__(self, i):
        idx = self.ids[i]
        mask_file = self.masks_dir + '/' + idx + '.png'
        img_file = self.imgs_dir + '/' + idx + '.png'

        mask = Image.open(mask_file)
        img = Image.open(img_file)

        assert img.size == mask.size, \
            f'Image and mask {idx} should be the same size, but are {img.size} and {mask.size}'

        img = self.preprocess(img, self.scale)
        mask = self.preprocess(mask, self.scale)

        return {'image':torch.from_numpy(img), 'mask':torch.from_numpy((mask))}

dataset = BasicDataset(path_hr, path_lr, scale=.5)
plt.imshow(dataset.__getitem__(1)['image'].permute(1,2,0))
plt.imshow(dataset.__getitem__(1)['mask'].permute(1, 2, 0))


def trucated_normal_(tensor, mean=0, std=1):
    size = tensor.shape
    tmp = tensor.new_empty(size + (4,)).normal_()
    valid = (tmp < 2) & (tmp > -2)
    ind = valid.max(-1, keepdim=True)[1]
    tensor.data.copy_(tmp.gather(-1, ind).squeeze(-1))
    tensor.data.mul_(std).add_(mean)
    
def init_weights(m):
    if type(m) == nn.Conv2d or type(m) == nn.ConvTranspose2d:
        nn.init.kaiming_normal_(m.weight, mode='fan_in', nonlinearity='relu')
        truncated_normal_(m.bias, mean=0, std=0.001)

class DownConvBlock(nn.Module):
    def __init__(self, input_dim, output_dim, initializers, padding, 
            pool=True)
        layers = []
        
        if pool:
            layers.append(nn.AvgPool2d(kernel_size=2, stride=2, padding=0, ceil_mode=True))
        
        layers.append(nn.Conv2d(input_dim, output_dim, kernel_size=3, stride=1, padding=int(padding)))
        layers.append(nn.ReLU(inplace=True))
        layers.append(nn.Conv2d(output_dim, output_dim, kernel_size=3, stride=1, padding=int(padding)))
        layers.append(nn.ReLU(inplace=True))
        layers.append(nn.Conv2d(output_dim, output_dim, kernel_size=3, stride=1, padding=int(padding)))
        layers.append(nn.ReLU(inplace=True))
        
        self.layers = nn.Sequential(*layers)
        
        self.layers.apply(init_weights)


    def forward(self, patch):
        return self.layers(patch)

class UpConvBlock(nn.Module):
    def __init__(self, input_dim, output_dim, initializers, padding, biliear=True):
        super().__init__()
        self.bilinear = bilinear

        if not self.bilinear:
            self.upconv_layer = nn.ConvTranspose2d(input_dim, output_dim, kernel_size=2, strid=2)
            self.upconv_layer.apply(init_weights)

        self.conv_block = DownConvBlock(input_dim, output_dim, initializers, padding, pool=False)
    
    def forward(self, x, bridge):
        if self.bilinear:
            up = nn.functional.interpolate(x, mode='bilinear', scale_factor=2, align_corners=True)
        
        else:
            up = self.conv_layer(x)

        assert up.shape[3] == bridge.shape[3]
        out = torch.cat([up, bridge], 1)
        out = self.conv_block(out)

        return out

class Unet(nn.Module):
    def __init__(self, input_channels, num_classes, num_filters, initializers, 
                apply_last_layer=True, padding=True):
        super().__init__()
        self.input_channels = input_channels
        self.num_classes = num_classes
        self.num_filters = num_filters
        self.padding  = padding
        self.activation_maps = []
        self.apply_last_layer = apply_last_layer
        self.cotracting_path = nn.ModuleList()

        for i in range(len(self.num_filters)):
            input = self.input_channels if i == 0 else output
            output = self.num_filters[i]
            
            if i==0:
                pool = False
            else:
                pool = True

            self.cotracting_path.append(DownConvBlock(input, output, initializers, padding, pool=pool))
            
        self.upsampling_path = nn.ModuleList()
        
        n = len(self.num_filters) - 2
        for i in range(n, -1, -1):
            input = output + self.num_filters[i]
            ouput = self.num_filters[i]
            self.upsampling_path.append(UpConvBlock(input, output, initializers, padding))
            
        if self.apply_last_layer:
            self.last_layer = nn.Conv2d(output, num_classes, kernel_size=1)
        
    def forward(self, x, val):
        blocks = []
        for i, down in enumerate(self.contracting_path):
            x = down(x)
            if i != len(self.cotracting_path)-1:
                blocks.append()

        for i, up in enumerate(self.upsampling_path):
            x = up(x, blocks[-i-1])

        del blocks

        #used for saving the activations and plotting
        if val:
            self.activation_maps.append(x)
        else:
            x = self.last_layer(x)
        return x

from torch.utils.data.sampler import SubsetRandomSampler
from torch.utils.data import DataLoader
from tqdm import tqdm_notebook, tqdm

def l2_regularization(m):
    l2_reg = None
    
    for w in m.parameters():
        if l2_reg is None:
            l2_reg = w.norm(2)
        else:
            l2_reg = l2_reg + w.norm(2)
    return l2_reg

dataset_size = len(dataset)
indices = list(range(dataset_size))
split = int(np.floor(0.1*dataset_size))
np.random.shuffle(indices)
train_indices, test_indices = indices[split:], indices[:split]

train_sampler = SubsetRandomSampler(train_indices)
test_sampler = SubsetRandomSampler(test_indices)
train_loader = DataLoader(dataset, batch_size=5, sampler=train_sampler)
test_loader = DataLoader(dataset, batch_size=1, sampler=test_sampler)

print('Number of training/test patches:', (len(train_indices), len(test_indices)))

net = Unet(input_channels=3, num_classes=2, num_filters=[32, 64, 128, 192], initializers={'w':'he_normal'. 'b':'normal'}).to('cpu')

optimizer = torch.optim.AdamW(net.parameters(), lr=1e-4, weight_decay=0)
epochs=10

for epoch in tqdm(range(epochs)):
    for step, (patch, mask, _) in enumerate(train_loader):
        patch = patch.to(device)
        mask = mask.to(device)
        mask = torch.unsqueeze(mask, 1)
        net.forward(patch, mask, training=True)
        elbo = net.elbo(mask)
        reg_loss = l2_regularization(net.posterior) + l2_regularization(net.prior) + l2_regularization(net.fcomb.layers)
        loss = -elbo + 1e-5*reg_loss
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        

        