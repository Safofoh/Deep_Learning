import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import MuHiPA.datasets.block as block

# from .fusions.factory import factory as factory_fusion
# from .mlp import MLP

class VRDNetBlock(nn.Module):

    def __init__(self, opt):
        super(VRDNetBlock, self).__init__()
        self.opt = opt
        self.classeme_embedding = nn.Embedding(
            self.opt['nb_classeme'],
            self.opt['classeme_dim'])
        self.fusion_c = block.factory_fusion(self.opt['classeme'])
        self.fusion_s = block.factory_fusion(self.opt['spatial'])
        self.fusion_f = block.factory_fusion(self.opt['feature'])
        # self.fusion_f2 = block.factory_fusion(self.opt['feature2'])
        # self.fusion_multi1 = block.factory_fusion(self.opt['fusion_multi1'])
        # self.fusion_multi2 = block.factory_fusion(self.opt['fusion_multi2'])
        self.predictor = MLP(**self.opt['predictor'])
        
        self.q_att_linear0 = nn.Linear(2152,100) #(4, 8)
        self.q_att_linear1 = nn.Linear(100, 2) #(8, 2)

        self.mlp_glimpses = 2
        self.fusion = block.fusions.Block(input_dims=[8,2148], output_dim=1, mm_dim=1600, chunks=20, rank=10)
        self.linear0 = nn.Linear(1, 512)
        self.linear1 = nn.Linear(512, self.mlp_glimpses)        
        #[4296,4296]
        self.fusion_integrate = block.fusions.Block(input_dims=[4304,4304], output_dim=200, mm_dim=1600, rank=1,chunks=5)
        
        
        self.merge_c = block.fusions.Block(input_dims=[200,200], output_dim=200, mm_dim=300, rank=3, chunks=5)
        self.merge_f = block.fusions.Block(input_dims=[200,200], output_dim=200, mm_dim=300, rank=3, chunks=5)
        self.merge_s = block.fusions.Block(input_dims=[200,200], output_dim=200, mm_dim=300, rank=3, chunks=5)
        


    def forward(self, batch):
        
 
        
        bsize = batch['subject_boxes'].size(0)
        x_c = [self.classeme_embedding(batch['subject_cls_id']),
               self.classeme_embedding(batch['object_cls_id'])]
        x_s = [batch['subject_boxes'], batch['object_boxes']]
        x_f = [batch['subject_features'], batch['object_features']]

        x_c = self.fusion_c(x_c)
        x_s = self.fusion_s(x_s)
        x_f = self.fusion_f(x_f)
        


        x = torch.cat([x_c, x_s, x_f], -1)

        if 'aggreg_dropout' in self.opt:
            x = F.dropout(x, self.opt['aggreg_dropout'], training=self.training)
        y = self.predictor(x)
        
        out = {
            'rel_scores': y
        }
        
        return out

    def image_attention(self, q, v):
        batch_size = q.size(0)
        n_regions = v.size(1)
        # print(n_regions)
        # print(q.shape)
        q = q[:,None,:].expand(q.size(0), n_regions, q.size(1))
        alpha = self.fusion([
            q.contiguous().view(batch_size*n_regions, -1),
            v.contiguous().view(batch_size*n_regions, -1)
        ])
        alpha = alpha.view(batch_size, n_regions, -1)
        if self.mlp_glimpses > 0:
            alpha = self.linear0(alpha)
            alpha = F.relu(alpha)
            alpha = self.linear1(alpha)

        alpha = F.softmax(alpha, dim=1)

        if alpha.size(2) > 1: # nb_glimpses > 1
            alphas = torch.unbind(alpha, dim=2)
            v_outs = []
            for alpha in alphas:
                
                alpha = alpha.unsqueeze(2).expand_as(v)
                v_out = alpha*v
                v_out = v_out.sum(1)
                v_outs.append(v_out)
            v_out = torch.cat(v_outs, dim=1)
        else:
            alpha = alpha.expand_as(v)
            v_out = alpha*v
            v_out = v_out.sum(1)
        return v_out 
    
    def qu_attention(self, q):
        # print(q.shape)
        q_att = self.q_att_linear0(q)
        q_att = F.relu(q_att)
        q_att = self.q_att_linear1(q_att)
        if q_att.size(2) > 1:
            q_atts = torch.unbind(q_att, dim=2)
            q_outs = []
            for q_att in q_atts:
                q_att = q_att.unsqueeze(2)
                q_att = q_att.expand_as(q)
                q_out = q_att*q
                q_out = q_out.sum(1)
                q_outs.append(q_out)
            q = torch.cat(q_outs, dim=1)
        return q

class MLP(nn.Module):
    
    def __init__(self,
            input_dim,
            dimensions,
            activation='relu',
            dropout=0.):
        super(MLP, self).__init__()
        self.input_dim = input_dim
        self.dimensions = dimensions
        self.activation = activation
        self.dropout = dropout
        # Modules
        self.linears = nn.ModuleList([nn.Linear(input_dim, dimensions[0])])
        for din, dout in zip(dimensions[:-1], dimensions[1:]):
            self.linears.append(nn.Linear(din, dout))
    
    def forward(self, x):
        for i,lin in enumerate(self.linears):
            x = lin(x)
            if (i < len(self.linears)-1):
                x = F.__dict__[self.activation](x)
                if self.dropout > 0:
                    x = F.dropout(x, self.dropout, training=self.training)
        return x