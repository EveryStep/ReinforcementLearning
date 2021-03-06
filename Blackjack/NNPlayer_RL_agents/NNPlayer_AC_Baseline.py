# -*- coding: utf-8 -*-

import math
import random
import numpy as np
import time
import matplotlib.pyplot as plt

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
import torchvision.transforms as T
from torch.distributions import Categorical
from torch.autograd import Variable

class ReplayMemory:
    def __init__(self,capacity=1000):
        self.position = -1
        self.capacity = capacity
        self.memory = []

    def push(self, *args):
        if len(self.memory) < self.capacity:
            self.memory.append(args)
            self.position = (self.position + 1) % self.capacity
        else:
            self.memory[self.position] = args
            self.position = (self.position + 1) % self.capacity

    def sample(self,sample_size):
        return random.sample(self.memory, sample_size)

class Policy_Net(torch.nn.Module):
    def __init__(self, lsize):
        super().__init__()
        self.layers = nn.ModuleList()
        self.n_layers = len(lsize) - 1
        for i in range(self.n_layers):
            self.layers.append(torch.nn.Linear(lsize[i], lsize[i+1]))

    def forward(self, x):
        for i in range(self.n_layers):
            x = self.layers[i](x)
            if i < self.n_layers-1:
                x = F.relu(x)
        x = F.softmax(x, dim=0)
        return x

    def save(self, fn):
        torch.save(self.state_dict(), fn)

    def load(self, fn):
        self.load_state_dict(torch.load(fn))

class V_Net(torch.nn.Module):
    def __init__(self, lsize):
        super().__init__()
        self.layers = nn.ModuleList()
        self.n_layers = len(lsize) - 1
        for i in range(self.n_layers):
            self.layers.append(torch.nn.Linear(lsize[i], lsize[i+1]))

    def forward(self, x):
        for i in range(self.n_layers):
            x = self.layers[i](x)
            if i < self.n_layers-1:
                x = F.relu(x)
        return x

    def save(self, fn):
        torch.save(self.state_dict(), fn)

    def load(self, fn):
        self.load_state_dict(torch.load(fn))

class PlayerMLP:
    def __init__(self, casino):
        self.casino = casino
        self.nA = casino.get_action_space()
        self.nDS = casino.nDS
        self.nPS = casino.nPS
        self.nPA = casino.nPA
        self.nSnA = (self.nDS, self.nPS, self.nPA, self.nA)
        self.n_episode = 0
        self.pocket = 0
        self.make_net()

        #self.optimizerpf = optim.SGD(self.pf.parameters(), lr=0.0001, momentum = 0.9, weight_decay = 0) # Add weight decay if you want regularization.
        #self.optimizervf = optim.SGD(self.vf.parameters(), lr=0.001, momentum = 0.9, weight_decay = 0)
        self.optimizervf = optim.Adam(self.vf.parameters(), lr=1e-2, weight_decay=0.9)
        self.optimizerpf = optim.Adam(self.pf.parameters(), lr=1e-4, weight_decay=0.9)

        milestone = [10000, 50000, 100000]
        self.lr_schedulervf = optim.lr_scheduler.MultiStepLR(self.optimizervf, milestone, gamma=1/np.sqrt(10))
        self.lr_schedulerpf = optim.lr_scheduler.MultiStepLR(self.optimizerpf, milestone, gamma=1/np.sqrt(10))
        #self.lr_schedulervf = optim.lr_scheduler.LambdaLR(self.optimizervf, lr_lambda = lambda epoch: 1/math.sqrt((epoch+1)/1000))
        #self.lr_schedulerpf = optim.lr_scheduler.LambdaLR(self.optimizerpf, lr_lambda = lambda epoch: 1/math.sqrt((epoch+1)/1000))

        self.batch_size = 1
        self.gamma = 0.95

    def make_net(self):
        H = 64
        lsizevf = [self.dimS(), H, H, H, 1]
        lsizepf = [self.dimS(), H,H,H, self.nA]
        self.vf = V_Net(lsizevf)
        self.pf = Policy_Net(lsizepf)

    def load(self, fn):
        self.pf.load(fn)

    def save(self, fn):
        self.pf.save(fn)

    def dimS(self):
        return 13

    def get_state(self):
        s = self.casino.observe()
        s = torch.tensor(s, dtype=torch.float32)
        p = self.casino.peep()
        p = torch.tensor(p, dtype=torch.float32)
        s = torch.cat((s,p))
        return s

    def get_action(self, state, Actor_Critic = False):
        if Actor_Critic == False:
            with torch.no_grad():
                q = self.qf(state)
            q = q.cpu()
            q = q.numpy()
            a = q.argmax()
        else:
            with torch.no_grad():
                a_prob = self.pf(state)
            a_prob = a_prob.cpu()
            a = torch.argmax(a_prob)
        return a

    def Policy_update(self,s,a,r,sp):
        # Q Actor-Critic
        v = self.vf(s)
        # Advantage
        if sp is None:
            v = r - self.vf(s)
        else:
            v = r + self.gamma * self.vf(sp) - v

        action = Variable(torch.FloatTensor([a]))
        probs = self.pf(s)
        m = Categorical(probs)

        loss = -m.log_prob(action) * v
        self.optimizerpf.zero_grad()
        loss.backward()
        self.optimizerpf.step()

    def TD_update_V(self, s,a,r,sp):
        s = torch.tensor(s)
        v = self.vf(s)
        if sp is None:
            t = torch.tensor(float(r))
        else:
            with torch.no_grad():
                t = r + self.gamma * self.vf(sp)
        #loss = F.mse_loss(q, t)
        loss = F.smooth_l1_loss(v, t)
        self.optimizervf.zero_grad()
        loss.backward()
        self.optimizervf.step()

    def Batch_update(self,batch_size):
        sample = self.Trans_Memory.sample(batch_size)
        for i in range(len(sample)):
            s,a,r,sp = sample[i]
            self.Policy_update(s,a,r,sp)
            self.TD_update_V(s,a,r,sp)

    def reset_episode(self):
        self.n_episode += 1

    def run_episode1(self, batch_size):
        self.lr_schedulervf.step()
        self.lr_schedulerpf.step()

        self.reset_episode()
        self.casino.start_game()
        s = self.get_state()
        done = False

        while not done:
            a_prob = self.pf(s)
            m = Categorical(a_prob)
            a = m.sample()
            _, reward, done = self.casino.step(a)
            if done: sp = None
            else:    sp = self.get_state()
            self.Trans_Memory.push(s,a,reward,sp)
            s = sp
        if len(self.Trans_Memory.memory) > batch_size*100:
            self.Batch_update(batch_size)

    def run_simulation(self, n_episode=1E7, max_time=6000000):
        stime = time.time()
        ip1 = 0
        self.Trans_Memory = ReplayMemory(1000)

        while time.time() - stime < max_time:
            ip1 += 1
            if ip1 > n_episode: break
            self.run_episode1(self.batch_size)
            if ip1 % 1000 == 0:
                print (ip1, 'lr = %f'%(self.optimizerpf.param_groups[0]['lr']))
            '''
            if ip1 % 10000 == 0:
                w = self.test_performance(10000) * 100
                print(ip1, 'winning rate = ', w)
                self.plot_Q()
            '''
        print("learning complete")

    def get_all_state_tensor(self, p):
        S = torch.zeros((self.nDS * self.nPS * self.nPA, self.dimS()))
        k = 0
        for ds in range(self.nDS):
            for ps in range(self.nPS):
                for ua in range(self.nPA):
                    S[k][0] = ds
                    S[k][1] = ps
                    S[k][2] = ua
                    k += 1
        return S

    def plot_Q(self, p=None, fid=0):
        if p is None:
            p = np.zeros(10)
            p.fill(1 / 13)
            p[8] = 4 / 13
        S = self.get_all_state_tensor(p)
        with torch.no_grad():
            Q = self.qf(S)
        Q = Q.numpy()
        Q = Q.reshape(self.nSnA)
        pi = Q.argmax(-1)
        Q[0:2, :, :, :] = -2
        Q[:, 0:4, :, :] = -2
        Q[:, 22, :, :] = -2
        Q[:, 4:12, 1, :] = -2
        pi[0:2, :] = -2
        pi[:, 0:4, :] = -2
        pi[:, 22, :] = -2
        pi[:, 4:12, 1] = -2

        fig = plt.figure(fid, figsize=(7, 8), clear=True)
        for ua in range(self.nPA):
            for a in range(self.nA):
                self.plot_Qi(fig, Q, a, ua)
            self.plot_pi(fig, pi, ua)
        self.diff_Q(fig, Q, pi)
        plt.draw()
        plt.pause(1)

    def plot_Qi(self, fig, Q, a, ua):
        ax = fig.add_subplot(6, 2, 2 * a + ua + 1)
        ax.imshow(Q[:, :, ua, a], vmin=-2, vmax=1)

    def plot_pi(self, fig, pi, ua):
        ax = fig.add_subplot(6, 2, 9 + ua)
        ax.imshow(pi[:, :, ua], vmin=-2, vmax=self.nA - 1)

    def diff_Q(self, fig, Q, pi):
        if 'pi_old' in dir(self):
            PIdiff = (pi != self.pi_old)
            Qdiff = (Q - self.Q_old)
            print("PI diff = %d" % (PIdiff.sum()))
            print('Qdiff max=%.3f, min=%.3f' % (Qdiff.max(), Qdiff.min()))
            ax = fig.add_subplot(6, 2, 11)
            ax.imshow(PIdiff[:, :, 0])
            ax = fig.add_subplot(6, 2, 12)
            ax.imshow(PIdiff[:, :, 1])
        self.Q_old = Q
        self.pi_old = pi

    def update_pocket(self, reward):
        self.pocket += reward

    def play_game(self):
        self.reset_episode()
        self.casino.start_game()
        done = False
        while not done:
            s = self.get_state()
            a = self.get_action(s, Actor_Critic = True)
            _, reward, done = self.casino.step(a)
        self.update_pocket(reward)
        return reward

    def print_epg_wr(self, n_games):
        epg = self.pocket / n_games
        wr = (epg + 1) / 2
        std_wr = np.sqrt(wr * (1 - wr) / n_games)
        print("# of game=%d, player's pocket=%d, E/G=%.5f, WR=%.5f%% +- %.5f"
              % (n_games, self.pocket, epg, wr * 100, std_wr * 100))
        return wr

    def test_performance(self, n_games):
        self.pocket = 0
        n_10 = n_games / 10
        for i in range(1, n_games + 1):
            reward = self.play_game()
            if n_games > 100000 and i % n_10 == 0:
                self.print_epg_wr(i)
        print ("Final result")
        return self.print_epg_wr(n_games)
