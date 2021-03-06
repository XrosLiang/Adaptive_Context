import torch.nn as nn
import torch
from torchvision import models
import torch.nn.functional as F
from misc.utils import *
from nets.adaptive_conv import AdaptiveConv2d


class ContextualModule(nn.Module):
    def __init__(self, features, out_features=512, sizes=(1, 2, 3, 6)):
        super(ContextualModule, self).__init__()
        self.scales = []
        self.scales = nn.ModuleList([self._make_scale(features, size) for size in sizes])
        self.bottleneck = nn.Conv2d(features * 2, out_features, kernel_size=1)
        self.relu = nn.ReLU()
        self.weight_net = nn.Conv2d(features,features,kernel_size=1)
        self.param_adapter = nn.Sequential(
                             nn.Conv2d(out_features, out_features, 3, 2),
                             nn.MaxPool2d(kernel_size=2, stride=2),
                             nn.Conv2d(out_features, out_features, 3, padding=1),
                             nn.MaxPool2d(kernel_size=3, stride=3),
                             nn.Conv2d(out_features, out_features, 3, padding=1))
        self.conv1x1_U = nn.Conv2d(out_features, out_features, 1, 1)
        self.conv1x1_V = nn.Conv2d(out_features, out_features, 1, 1)



    def __make_weight(self,feature,scale_feature):
        weight_feature = feature - scale_feature
        return F.sigmoid(self.weight_net(weight_feature))

    def _make_scale(self, features, size):
        prior = nn.AdaptiveAvgPool2d(output_size=(size, size))
        conv = nn.Conv2d(features, features, kernel_size=1, bias=False)
        return nn.Sequential(prior, conv)

    def forward(self, feats):
        h, w = feats.size(2), feats.size(3)
        multi_scales = [F.upsample(input=stage(feats), size=(h, w), mode='bilinear') for stage in self.scales]
        weights = [self.__make_weight(feats,scale_feature) for scale_feature in multi_scales]
        parsing_feat = (multi_scales[0] * weights[0] + multi_scales[1] * weights[1] + multi_scales[2] * weights[
            2] + multi_scales[3] * weights[3]) / (weights[0] + weights[1] + weights[2] + weights[3])
        #print('77777777777777777777777777777777',parsing_feat.size())
        all_weights = (weights[0] +  weights[1] + weights[2] +  weights[3])/4
        theta_prime = self.param_adapter(parsing_feat)
        input_feat = self.conv1x1_U(feats)
        adaptive_conv = AdaptiveConv2d(input_feat.size(0) * input_feat.size(1),
                                       input_feat.size(0) * input_feat.size(1),
                                       3, padding=[2,3],
                                       groups=input_feat.size(0) * input_feat.size(1),
                                       bias=False)
        pose_feat_res = adaptive_conv(input_feat, theta_prime)
        #print('88888888888888888',pose_feat_res.size(),theta_prime.size(),input_feat.size())
        pose_feat_res = self.conv1x1_V(pose_feat_res)
        pose_feat_res = self.relu(pose_feat_res)
        #print('999999999999999999999shape and size',pose_feat_res.size(),feats.size())
        #overall_features = [(multi_scales[0]*weights[0]+multi_scales[1]*weights[1]+multi_scales[2]*weights[2]+multi_scales[3]*weights[3])/(weights[0]+weights[1]+weights[2]+weights[3])]+ [feats]
        overall_features = [pose_feat_res] + [feats]
        bottle = self.bottleneck(torch.cat(overall_features, 1))
        return self.relu(bottle)


class SCAR(nn.Module):
    def __init__(self, load_weights=False):
        super(SCAR, self).__init__()
        self.seen = 0
        self.context = ContextualModule(512, 512)
        self.frontend_feat = [64, 64, 'M', 128, 128, 'M', 256, 256, 256, 'M', 512, 512, 512]
        self.backend_feat  = [512, 512, 512,256,128,64]
        self.frontend = make_layers(self.frontend_feat)
        self.backend = make_layers(self.backend_feat,in_channels = 512,dilation = True)
        self.output_layer = SCAModule(64, 1)
        # self.output_layer = nn.Conv2d(64, 1, kernel_size=1)
        if not load_weights:
            mod = models.vgg16(pretrained = True)
            initialize_weights(self.modules())
            self.frontend.load_state_dict(mod.features[0:23].state_dict())
    def forward(self,x):
        x = self.frontend(x)
        x = self.context(x)
        x = self.backend(x)
        x = self.output_layer(x)
        x = F.interpolate(x,scale_factor=8, mode='bilinear')
        return x  


class SCAModule(nn.Module):
    def __init__(self, inn, out):
        super(SCAModule, self).__init__()
        base = inn // 4
        self.conv_sa = nn.Sequential(Conv2d(inn, base, 3, same_padding=True, bias=False),
                                     SAM(base),
                                     Conv2d(base, base, 3, same_padding=True, bias=False)
                                     )       
        self.conv_ca = nn.Sequential(Conv2d(inn, base, 3, same_padding=True, bias=False),
                                     CAM(base),
                                     Conv2d(base, base, 3, same_padding=True, bias=False)
                                     )
        self.conv_cat = Conv2d(base*2, out, 1, same_padding=True, bn=False)

    def forward(self, x):
        sa_feat = self.conv_sa(x)
        ca_feat = self.conv_ca(x)
        cat_feat = torch.cat((sa_feat,ca_feat),1)
        cat_feat = self.conv_cat(cat_feat)
        return cat_feat   

class SAM(nn.Module):
    def __init__(self, channel):
        super(SAM, self).__init__()
        self.para_lambda = nn.Parameter(torch.zeros(1))
        self.query_conv = Conv2d(channel, channel//8, 1, NL='none')
        self.key_conv = Conv2d(channel, channel//8, 1, NL='none')
        self.value_conv = Conv2d(channel, channel, 1, NL='none')

    def forward(self, x):
        N, C, H, W = x.size() 
        proj_query = self.query_conv(x).view(N, -1, W*H).permute(0, 2, 1)
        proj_key = self.key_conv(x).view(N, -1, W*H)
        energy = torch.bmm(proj_query, proj_key)
        attention = F.softmax(energy,dim=-1)
        proj_value = self.value_conv(x).view(N, -1, W*H)

        out = torch.bmm(proj_value, attention.permute(0, 2, 1))
        out = out.view(N, C, H, W)

        out = self.para_lambda*out + x
        return out

class CAM(nn.Module):
    def __init__(self, in_dim):
        super(CAM, self).__init__()
        self.para_mu = nn.Parameter(torch.zeros(1))

    def forward(self,x):
        N, C, H, W = x.size() 
        proj_query = x.view(N, C, -1)
        proj_key = x.view(N, C, -1).permute(0, 2, 1)
        energy = torch.bmm(proj_query, proj_key)
        energy_new = torch.max(energy, -1, keepdim=True)[0].expand_as(energy)-energy
        attention = F.softmax(energy,dim=-1)
        proj_value = x.view(N, C, -1)

        out = torch.bmm(attention, proj_value)
        out = out.view(N, C, H, W)

        out = self.para_mu*out + x
        return out

class Conv2d(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride=1, NL='relu', same_padding=False, bn=True, bias=True):
        super(Conv2d, self).__init__()
        padding = int((kernel_size - 1) // 2) if same_padding else 0

        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size, stride, padding=padding, bias=bias)

        self.bn = nn.BatchNorm2d(out_channels) if bn else None
        if NL == 'relu' :
            self.relu = nn.ReLU(inplace=True) 
        elif NL == 'prelu':
            self.relu = nn.PReLU() 
        else:
            self.relu = None

    def forward(self, x):
        x = self.conv(x)
        if self.bn is not None:
            x = self.bn(x)
        if self.relu is not None:
            x = self.relu(x)
        return x

def make_layers(cfg, in_channels = 3, batch_norm=False, dilation = False):
    if dilation:
        d_rate = 2
    else:
        d_rate = 1
    layers = []
    for v in cfg:
        if v == 'M':
            layers += [nn.MaxPool2d(kernel_size=2, stride=2)]
        else:
            conv2d = nn.Conv2d(in_channels, v, kernel_size=3, padding=d_rate,dilation = d_rate)
            if batch_norm:
                layers += [conv2d, nn.BatchNorm2d(v), nn.ReLU(inplace=True)]
            else:
                layers += [conv2d, nn.ReLU(inplace=True)]
            in_channels = v
    return nn.Sequential(*layers)   