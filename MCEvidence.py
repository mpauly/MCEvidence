#!usr/bin/env python
"""
Version : 0.1.1
Date : 1st March 2017

Authors : Yabebal Fantaye
Email : yabi@aims.ac.za
Affiliation : African Institute for Mathematical Sciences - South Africa
              Stellenbosch University - South Africa

License : MIT

Status : Under Development

Description :
Python implementation of the evidence estimation from MCMC chains 
as preesented in A. Heavens et. al. 2017
(paper can be found here : https://arxiv.org/abs/ ).

This code is tested in Python 2 version 2.7.12 and Python 3 version 3.5.2  
"""

from __future__ import absolute_import
from __future__ import print_function
import subprocess
import importlib
import itertools
from functools import reduce
import io

import tempfile 
import os
import glob
import sys
import math
import numpy as np
import pandas as pd
import sklearn as skl
import statistics
from sklearn.neighbors import NearestNeighbors, DistanceMetric
import scipy.special as sp
from numpy.linalg import inv
from numpy.linalg import det
import logging
from argparse import ArgumentParser

#====================================
try:
    '''
    If getdist is installed, use that to reach chains.
    Otherwise, use the minimal chain reader class implemented below.
    '''    
    from getdist import MCSamples, chains
    from getdist import plots, IniFile
    import getdist as gd
    use_getdist=True
except:    
    '''
    getdist is not installed
    use a simple chain reader
    '''
    use_getdist=False    
#====================================

FORMAT = "%(levelname)s:%(filename)s.%(funcName)s():%(lineno)-8s %(message)s"
logging.basicConfig(level=logging.DEBUG,format=FORMAT)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


__author__ = "Yabebal Fantaye"
__email__ = "yabi@aims.ac.za"
__license__ = "MIT"
__version__ = "0.1"
__status__ = "Development"

desc='Planck Chains MCEvidence. Returns the log Bayesian Evidence computed using the kth NN'
cite='''
**
When using this code in published work, please cite the following paper: **
Heavens et. al. (2017) 
Marginal Likelihoods from Monte Carlo Markov Chains
https://arxiv.org/abs/1704.03472
''' 

#np.random.seed(1)

# Create a base class
class LoggingHandler(object):
    def set_logger(self):
        self.logger = logging.getLogger(self.log_message()) #self.__class__.__name__
    def log_message(self):
        import inspect
        stack = inspect.stack()
        return str(stack[2][4])

class data_set(object):
    def __init__(self,d):
        self.samples=d['samples']
        self.weights=d['weights']
        self.loglikes=d['loglikes']
        self.adjusted_weights=d['aweights']

        
class SamplesMIXIN(object):
    '''The following routines must be defined to use this class:
       __init__:  where certain variables are defined
       load_from_file: where data is read from file and 
                       returned as python dict
    '''
    
    def setup(self,str_or_dict,**kwargs):
        #Get the getdist MCSamples objects for the samples, specifying same parameter
        #names and labels; if not specified weights are assumed to all be unity

        logging.basicConfig(level=logging.INFO,format=FORMAT)        
        self.logger = logging.getLogger(__name__) #+self.__class__.__name__)
        #self.logger.addHandler(handler)
        
        if self.debug:        
            self.logger.setLevel(logging.DEBUG)
            
        #read MCMC samples from file or dict
        if isinstance(str_or_dict,str):                
            fileroot=str_or_dict
            self.logger.info('Loading chain from '+fileroot)                
            s1,s2 = self.load_from_file(fileroot,**kwargs)
            self.data={'s1':data_set(s1),'s2':data_set(s2)}
            
        # TODO: implement s1_s2 when dict is passed
        #elif isinstance(str_or_dict,dict):                
        #    d=str_or_dict
        
        else:
           self.logger.info('Passed first argument type is: %s'%type(str_or_dict))                
           self.logger.error('first argument to samples2getdist should be a string or dict.')
           raise

        #self.samples=s1_s2['s1']['samples']
        #self.loglikes=s1_s2['s1']['loglikes']
        #self.weights=s1_s2['s1']['weights']
        
        #if 'weights' in d.keys() else np.ones(len(self.loglikes))


        ndim=self.get_shape()[1]

        if hasattr(self, 'names'):
            if self.names is None:
                self.names = ["%s%s"%('p',i) for i in range(ndim)]
        if hasattr(self, 'labels'):                
            if self.labels is None:
                self.labels =  ["%s_%s"%(self.px,i) for i in range(ndim)]
                
        if not hasattr(self, 'trueval'):     
            self.trueval=None
            
        self.nparamMC=self.get_shape()[1]

    
    def get_shape(self,name='s1'):
        return self.data[name].samples.shape

    def importance_sample(self,isfunc,name='s1'):        
        #importance sample with external function       
        self.logger.info('Importance sampling partition: '.format(name))
        negLogLikes=isfunc(self.data[name].samples)
        scale=0 #negLogLikes.min()
        self.data[name].adjusted_weights *= np.exp(-(negLogLikes-scale))                     

    def thin(self,nthin=1,name='s1'):
        self.logger.info('Thinning sample partition: '.format(name))         
        if nthin==1:
            return
        try:
            norig=len(self.data[name].weights)
            if nthin<1:
                thin_ix,new_weights = self.poisson_thin(nthin,name=name)
            else:
                #call weighted thinning                    
                try:
                    #if weights are integers, use getdist algorithm
                    thin_ix,new_weights = self.thin_indices(nthin,name=name)
                except:
                    #if weights are not integers, use internal algorithm
                    thin_ix,new_weights = self.weighted_thin(nthin,name=name)

            #now thin samples and related quantities
            self.data[name].weights = new_weights            
            self.data[name].samples=self.data[name].samples[thin_ix, :]
            self.data[name].loglikes=self.data[name].loglikes[thin_ix]
            self.data[name].adjusted_weights=self.data[name].weights.copy()
            
            nnew=len(new_weights)
            self.logger.info('''Thinning with thin length={} 
                                #old_chain={},#new_chain={}'''.format(nthin,norig,nnew))                
        except:
            self.logger.info('Thinning not possible.')
            raise

    def removeBurn(self,remove=0,name='s1'):
        self.logger.info('burning for sample partition={}'.format(name)) 
        nstart=remove
        if remove<1:
            nstart=int(len(self.data[name].loglikes)*remove)
        else:
            pass
        self.logger.info('Removing %s lines as burn in' % remove)

        nsamples=self.data[name].samples.shape[0]
        try:
            self.data[name].samples=self.data[name].samples[nstart:, :]
            self.data[name].loglikes=self.data[name].loglikes[nstart:]
            self.data[name].weights=self.data[name].weights[nstart:]
        except:
            self.logger.info('burn-in failed: burn length %s > sample length %s' % (nstart,nsamples))
            raise

    def arrays(self,name='s1'):
        self.logger.debug('extracting arrays for sample partition: '.format(name))
        s=self.data[name].samples
        lnp=-self.data[name].loglikes
        w=self.data[name].weights
        return s, lnp, w
    
    def poisson_thin(self,thin_retain_frac,name='s1'):
        '''
        Given a weight array and thinning retain fraction, perform thinning.
        The algorithm works by randomly sampling from a Poisson distribution 
        with mean equal to the weight.
        '''
        weights=self.data[name].weights.copy()

        w       = weights*thin_retain_frac
        new_w   = np.array([float(np.random.poisson(x)) for x in w])
        thin_ix = np.where(new_w>0)[0]
        new_w = new_w[thin_ix]
        
        text='''Thinning with Poisson Sampling: thinfrac={}. 
                    new_nsamples={},old_nsamples={}'''
        self.logger.debug(text.format(thin_retain_frac,len(thin_ix),len(w)))

        if self.debug:
            print('Poisson thinned chain:', len(thin_ix),
                      '<w>', '{:5.2f}'.format(np.mean(weights)),
                      '{:5.2f}'.format(np.mean(new_w)))

            print('Sum of old weights:',np.sum(weights))
            print('Sum of new weights:',np.sum(new_w))
            print('Thinned:','{:5.3f}'.format(np.sum(new_w)/np.sum(weights)))

    #    return {'ix':thin_ix, 'w':weights[thin_ix]}
        return thin_ix, new_w

    def weighted_thin(self,thin_unit,name='s1'):
        '''
        Given a weight array, perform thinning.
        If the all weights are equal, this should 
        be equivalent to selecting every N/((thinfrac*N)
        where N=len(weights).
        '''
        weights=self.data[name].weights.copy()

        N=len(weights)
        if thin_unit==0: return range(N),weights

        if thin_unit<1:
            N2=np.int(N*thin_unit)
        else:
            N2=N//thin_unit

        #bin the weight index to have the desired length
        #this defines the bin edges
        bins = np.linspace(-1, N, N2+1) 
        #this collects the indices of the weight array in each bin
        ind = np.digitize(np.arange(N), bins)  
        #this gets the maximum weight in each bin
        thin_ix=pd.Series(weights).groupby(ind).idxmax().tolist()
        thin_ix=np.array(thin_ix,dtype=np.intp)
        new_w = weights[thin_ix]
        
        text='''Thinning with weighted binning: thinfrac={}. 
                new_nsamples={},old_nsamples={}'''
        self.logger.info(text.format(thin_unit,len(thin_ix),len(new_w)))

        return thin_ix, new_w

    def thin_indices(self, factor,name='s1'):
        """
        Ref: 
        http://getdist.readthedocs.io/en/latest/_modules/getdist/chains.html#WeightedSamples.thin

        Indices to make single weight 1 samples. Assumes integer weights.

        :param factor: The factor to thin by, should be int.
        :param weights: The weights to thin, 
        :return: array of indices of samples to keep
        """
        weights=self.data[name].weights.copy()
        
        numrows = len(weights)
        norm1 = np.sum(weights)
        weights = weights.astype(np.int)
        norm = np.sum(weights)

        if abs(norm - norm1) > 1e-4:
            print('Can only thin with integer weights')
            raise 
        if factor != int(factor):
            print('Thin factor must be integer')
            raise 
        factor = int(factor)
        if factor >= np.max(weights):
            cumsum = np.cumsum(weights) // factor
            # noinspection PyTupleAssignmentBalance
            _, thin_ix = np.unique(cumsum, return_index=True)
        else:
            tot = 0
            i = 0
            thin_ix = np.empty(norm // factor, dtype=np.int)
            ix = 0
            mult = weights[i]
            while i < numrows:
                if mult + tot < factor:
                    tot += mult
                    i += 1
                    if i < numrows: mult = weights[i]
                else:
                    thin_ix[ix] = i
                    ix += 1
                    if mult == factor - tot:
                        i += 1
                        if i < numrows: mult = weights[i]
                    else:
                        mult -= (factor - tot)
                    tot = 0

        return thin_ix,weights[thin_ix]

#==================

class MCSamples(SamplesMIXIN):

    def __init__(self,str_or_dict,trueval=None,
                     debug=False,split=False,
                     names=None,labels=None,px='x',
                     **kwargs):
        
        self.debug=debug
        self.split=split
        self.names=None
        self.labels=None
        self.trueval=trueval       
        self.px=px
            
        self.setup(str_or_dict,**kwargs)

    def chains2samples(self):
        """
        Combines separate chains into one samples array, so self.samples has all the samples
        and this instance can then be used as a general :class:`~.chains.WeightedSamples` instance.

        :return: self
        """
        if self.chains is None:
            self.logger.error('The chains array is empty!')
            raise
        #
        nchains=len(self.chains)
        #
        self.chain_offsets = np.cumsum(np.array([0] + [chain.shape[0] for chain in self.chains]))
        if self.split:
            if nchains>1:
                text='''partition {} chains in two sets  
                        chains 1:{} in one,  the {}th chain in the other'''
                self.logger.info(text.format(nchains,nchains-1,nchains)) 
                s1=np.concatenate(self.chains[0:-1])
                s2=self.chains[-1]
            else:
                s=self.chains[0]
                nrow=len(s)
                rowid=range(nrow)
                ix=np.random.choice(rowid,size=nrow/2,replace=False)
                not_ix = np.setxor1d(rowid, ix) 
                #now split
                text='single chain with nrow={} split to ns1={}, ns2={}'
                self.logger.info(text.format(nrow, len(ix),len(not_ix)))
                s1=s[ix,:]
                s2=s[not_ix,:]
            #change to dict
            s1_dict =  {'weights':s1[:,0],'loglikes':s1[:,1],'samples':s1[:,2:]}
            s2_dict =  {'weights':s2[:,0],'loglikes':s2[:,1],'samples':s2[:,2:]}                        
        else:
            #no split, so just assign s1 and s2 to same array
            s1=np.concatenate(self.chains)
            s1_dict = {'weights':s1[:,0],'loglikes':s1[:,1],'samples':s1[:,2:]}
            s2_dict = {'weights':None,'loglikes':None,'samples':None}
            
        #free array    
        self.chains = None

        # a copy of the weights that can be altered to
        # independently to the original weights
        s1_dict['aweights']=np.copy(s1_dict['weights'])
        s2_dict['aweights']=np.copy(s2_dict['weights'])        
       
        return s1_dict,s2_dict
    
    def load_from_file(self,fname,**kwargs):
        self.logger.debug('''Loading file assuming CosmoMC columns order: 
                           weight loglike param1 param2 ...''')
        self.chains=[]
        
        try:
            d=np.loadtxt(fname)
            self.chains.append(d)            
            self.logger.info(' loaded file: '+fname)
        except:
            idchain=kwargs.pop('idchain', 0)
            if idchain>0:
                f=fname+'_{}.txt'.format(idchain)
                d=np.loadtxt(f)
                self.chains.append(d)                
                self.logger.info(' loaded file: '+f)                    
            else:
                self.logger.info(' loading files: '+fname+'*.txt')                    
                for f in glob.glob(fname+'*.txt'):
                    self.logger.debug('loading: '+f)
                    self.chains.append(np.loadtxt(f))

        return self.chains2samples()
            
           
#============================================================
#======  Here starts the main Evidence calculation code =====
#============================================================

class MCEvidence(object):
    def __init__(self,method,ischain=True,isfunc=None,
                     thinlen=0.0,burnlen=0.0,split=False,
                     ndim=None, kmax= 5, 
                     priorvolume=1,debug=False,
                     nsample=None,
                      nbatch=1,
                      brange=None,
                      bscale='',
                      verbose=1,args={},
                      **gdkwargs):
        """Evidence estimation from MCMC chains
        :param method: chain name (str) or array (np.ndarray) or python class
                If string or numpy array, it is interpreted as MCMC chain. 
                Otherwise, it is interpreted as a python class with at least 
                a single method sampler and will be used to generate chain.

        :param ischain (bool): True indicates the passed method is to be interpreted as a chain.
                This is important as a string name can be passed for to 
                refer to a class or chain name 

        :param nbatch (int): the number of batchs to divide the chain (default=1) 
               The evidence can be estimated by dividing the whole chain 
               in n batches. In the case nbatch>1, the batch range (brange) 
               and batch scaling (bscale) should also be set

        :param brange (int or list): the minimum and maximum size of batches in linear or log10 scale
               e.g. [3,4] with bscale='logscale' means minimum and maximum batch size 
               of 10^3 and 10^4. The range is divided nbatch times.

        :param bscale (str): the scaling in batch size. Allowed values are 'log','linear','constant'/

        :param kmax (int): kth-nearest-neighbours, with k between 1 and kmax-1

        :param args (dict): argument to be passed to method. Only valid if method is a class.
        
        :param gdkwargs (dict): arguments to be passed to getdist.

        :param verbose: chattiness of the run
        
        """
        logging.basicConfig(level=logging.DEBUG,format=FORMAT)
        self.logger = logging.getLogger(__name__) # +self.__class__.__name__)
        #self.logger.addHandler(handler)
        
        self.verbose=verbose
        self.debug=False
        if debug or verbose>1:
            self.debug=True
            self.logger.setLevel(logging.DEBUG)    
        if verbose==0:
            self.logger.setLevel(logging.WARNING)            

        #print('log level: ',logging.getLogger().getEffectiveLevel())
        
        self.info={}
        #
        self.split=split
        self.nbatch=nbatch
        self.brange=brange #todo: check for [N] 
        self.bscale=bscale if not isinstance(self.brange,int) else 'constant'
        
        # The arrays of powers and nchain record the number of samples 
        # that will be analysed at each iteration. 
        #idtrial is just an index
        self.idbatch=np.arange(self.nbatch,dtype=int)
        self.powers  = np.zeros(self.nbatch)
        self.bsize  = np.zeros(self.nbatch,dtype=int)
        self.nchain  = np.zeros(self.nbatch,dtype=int)               
        #
        self.kmax=max(2,kmax)
        self.priorvolume=priorvolume
        #
        self.ischain=ischain
        #
        self.fname=None
        #
        if ischain:
            
            if isinstance(method,str):
                self.fname=method      
                self.logger.debug('Using chains: %s'%method)
            else:
                self.logger.debug('dictionary of samples and loglike array passed')
                
        else: #python class which includes a method called sampler
            
            if nsample is None:
                self.nsample=100000
            else:
                self.nsample=nsample
            
            #given a class name, get an instance
            if isinstance(method,str):
                XClass = getattr(sys.modules[__name__], method)
            else:
                XClass=method
            
            if hasattr(XClass, '__class__'):
                self.logger.debug('method is an instance of a class')
                self.method=XClass
            else:
                self.logger.debug('method is class variable .. instantiating class')
                self.method=XClass(*args)                
                #if passed class has some info, display it
                try:
                    print()
                    msg=self.method.info()                        
                    print()
                except:
                    pass                        
                # Now Generate samples.
                # Output should be dict - {'chains':,'logprob':,'weight':} 
                method=self.method.Sampler(nsamples=self.nsamples)                                 
                
        #======== By this line we expect only chains either in file or dict ====
        self.gd = MCSamples(method,split=self.split,debug=self.debug,**gdkwargs)

        if burnlen>0:
            _=self.gd.removeBurn(remove=burnlen,name='s1')
            if self.split: _=self.gd.removeBurn(remove=burnlen,name='s2')            
        if np.abs(thinlen)>0:
            self.logger.debug('applying weighted thinning with thin length=%s'%thinlen)
            _=self.gd.thin(nthin=thinlen,name='s1')
            if self.split: _=self.gd.thin(nthin=thinlen,name='s2')               

        if isfunc:
            #try:
            self.gd.importance_sample(isfunc,name='s1')
            if self.split: self.gd.importance_sample(isfunc,name='s2')            
            #except:
            #    self.logger.warn('Importance sampling failed. Make sure getdist is installed.')
               
        self.info['NparamsMC']=self.gd.nparamMC
        self.info['Nsamples_read']=self.gd.get_shape()[0]
        self.info['Nparams_read']=self.gd.get_shape()[1]
        #

        #after burn-in and thinning
        self.nsample = self.gd.get_shape()[0]            
        if ndim is None: ndim=self.gd.nparamMC        
        self.ndim=ndim        
        #
        self.info['NparamsCosmo']=self.ndim
        self.info['Nsamples']=self.nsample
        
        if self.debug:
            print('partition s1.shape',self.gd.get_shape(name='s1'))
            if split:
                print('partition s2.shape',self.gd.get_shape(name='s2'))            
        #
        self.logger.info('chain array dimensions: %s x %s ='%(self.nsample,self.ndim))            
        #
        self.set_batch()


    def summary(self):
        print()
        print('ndim={}'.format(self.ndim))
        print('nsample={}'.format(self.nsample))
        print('kmax={}'.format(self.kmax))
        print('brange={}'.format(self.brange))
        print('bsize'.format(self.bsize))
        print('powers={}'.format(self.powers))
        print('nchain={}'.format(self.nchain))
        print()
        
    def get_batch_range(self):
        if self.brange is None:
            powmin,powmax=None,None
        else:
            powmin=np.array(self.brange).min()
            powmax=np.array(self.brange).max()
            if powmin==powmax and self.nbatch>1:
                self.logger.error('nbatch>1 but batch range is set to zero.')
                raise
        return powmin,powmax
    
    def set_batch(self,bscale=None):
        if bscale is None:
            bscale=self.bscale
        else:
            self.bscale=bscale
            
        #    
        if self.brange is None: 
            self.bsize=self.brange #check
            powmin,powmax=None,None
            self.nchain[0]=self.nsample
            self.powers[0]=np.log10(self.nsample)
        else:
            if bscale=='logpower':
                powmin,powmax=self.get_batch_range()
                self.powers=np.linspace(powmin,powmax,self.nbatch)
                self.bsize = np.array([int(pow(10.0,x)) for x in self.powers])
                self.nchain=self.bsize

            elif bscale=='linear':   
                powmin,powmax=self.get_batch_range()
                self.bsize=np.linspace(powmin,powmax,self.nbatch,dtype=np.int)
                self.powers=np.array([int(log10(x)) for x in self.nchain])
                self.nchain=self.bsize

            else: #constant
                self.bsize=self.brange #check
                self.powers=self.idbatch
                self.nchain=np.array([x for x in self.bsize.cumsum()])
            
    def get_samples(self,nsamples,istart=0,
                        rand=False,name='s1',
                        prewhiten=True):    
        # If we are reading chain, it will be handled here 
        # istart -  will set row index to start getting the samples 

        ntot=self.gd.get_shape()[0]
        
        if rand and not self.brange is None:
            if nsamples>ntot:
                self.logger.error('partition %s nsamples=%s, ntotal_chian=%s'%(name,nsamples,ntot))
                raise
            
            idx=np.random.randint(0,high=ntot,size=nsamples)
        else:
            idx=np.arange(istart,nsamples+istart)

        self.logger.debug('partition %s requested nsamples=%s, ntotal_chian=%s'%(name,nsamples,ntot))
        s,lnp,w=self.gd.arrays(name)

        #trim 
        s,lnp,w = s[idx,0:self.ndim],lnp[idx],w[idx]
        
        if prewhiten:
            self.logger.debug('Prewhitenning chain partition: %s '%name)
            try:
                # Covariance matrix of the samples, and eigenvalues (in w) and eigenvectors (in v):
                ChainCov = np.cov(s.T)
                
                eigenVal,eigenVec = np.linalg.eig(ChainCov)
                #check for negative eigenvalues
                if (eigenVal<0).any():
                    self.logger.warn("Some of the eigenvalues of the covariance matrix are negative and/or complex:")
                    for i,e in enumerate(eigenVal):
                        print("Eigenvalue Param_{} = {}".format(i,e))
                    print("")
                    print("=================================================================================")
                    print("        Chain is not diagonalized! Estimated Evidence may not be accurate!       ")
                    print("              Consider using smaller set of parameters using --ndim              ")
                    print("=================================================================================")
                    print("")                    
                    #no diagonalisation
                    Jacobian=1                           
                else:
                    #all eigenvalues are positive
                    Jacobian = math.sqrt(np.linalg.det(ChainCov))
                    # Prewhiten:  First diagonalise:
                    s = np.dot(s,eigenVec);
                    # And renormalise new parameters to have unit covariance matrix:
                    for i in range(self.ndim):
                        s[:,i]= s[:,i]/math.sqrt(eigenVal[i])
                
            except:
                    self.logger.error("Unknown error during diagonalizing the chain with its covariance matrix.")
        else:
            #no diagonalisation
            Jacobian=1
        
                
        return s,lnp,w,Jacobian
        

    def evidence(self,verbose=None,rand=False,info=False,
                      profile=False,pvolume=None,pos_lnp=False,
                      nproc=-1,prewhiten=True):
        '''

        MARGINAL LIKELIHOODS FROM MONTE CARLO MARKOV CHAINS algorithm described in Heavens et. al. (2017)

        If SPLIT=TRUE:
          EVIDENCE IS COMPUTED USING TWO INDEPENDENT CHAINS. THIS MEANS
          NEAREST NEIGHBOUR OF POINT "A" IN AN MCMC SAMPLE MC1 IS SEARCHED IN MCMC SAMPLE MC2.
          THE ERROR ON THE EVIDENCE FROM (AUTO) EVIDENCE IS LARGER THAT CROSS EVIDENCE BY SQRT(2)
          DUE TO THE FOLLOWING:
              if the nearest neighbour to A is B, then the NN to B will be A
       
        Parameters
        ---------

        :param verbose - controls the amount of information outputted during run time
        :param rand - randomised sub sampling of the MCMC chains
        :param info - if True information about the analysis will be returd to the caller
        :param pvolume - prior volume
        :param pos_lnp - if input log likelihood is multiplied by negative or not
        :param nproc - determined how many processors the scikit package should use or not
        :param prewhiten  - if True chains will be normalised to have unit variance
        
        Returns
        ---------

        MLE - maximum likelihood estimate of evidence:
        self.info (optional) - returned if info=True. Contains useful information about the chain analysed
               

        Notes
        ---------

        The MCEvidence algorithm is implemented using scikit nearest neighbour code.


        Examples
        ---------

        To run the evidence estimation from an ipython terminal or notebook

        >> from MCEvidence import MCEvidence
        >> MLE = MCEvidence('/path/to/chain').evidence()
        

        To run MCEvidence from shell

        $ python MCEvidence.py </path/to/chain> 

        References
        -----------

        .. [1] Heavens etl. al. (2017)
        
        '''     
            
        if verbose is None:
            verbose=self.verbose

        #get prior volume
        if pvolume is None:
            logPriorVolume=math.log(self.priorvolume)
        else:
            logPriorVolume=math.log(pvolume)            

        self.logger.debug('log prior volume: %s'%logPriorVolume)
            
        kmax=self.kmax
        ndim=self.ndim
        
        MLE = np.zeros((self.nbatch,kmax))

        #get covariance matrix of chain
        #ChainCov=self.gd.samples.getCovMat()
        #eigenVal,eigenVec = np.linalg.eig(ChainCov)
        #Jacobian = math.sqrt(np.linalg.det(ChainCov))
        #ndim=len(eigenVal)
        
        # Loop over different numbers of MCMC samples (=S):
        itot=0
        for ipow,nsample in zip(self.idbatch,self.nchain):                
            S=int(nsample)            
            DkNN    = np.zeros((S,kmax))
            indices = np.zeros((S,kmax))
            volume  = np.zeros((S,kmax))

            samples,logL,weight,Jacobian=self.get_samples(S,istart=itot,
                                                            rand=rand,
                                                            prewhiten=prewhiten,
                                                              name='s1')
            
            #We need the logarithm of the likelihood - not the negative log
            if pos_lnp: logL=-logL
                
            # Renormalise loglikelihood (temporarily) to avoid underflows:
            logLmax = np.amax(logL)
            fs    = logL-logLmax
                        
            #print('samples, after prewhiten', samples[1000:1010,0:ndim])
            #print('Loglikes ',logLmax,logL[1000:1010],fs[1000:1010])
            #print('weights',weight[1000:1010])
            #print('EigenValues=',eigenVal)
            
            # Use sklearn nearest neightbour routine, which chooses the 'best' algorithm.
            # This is where the hard work is done:
            if self.split:
                samples2,logL2,weight2,Jacobian2=self.get_samples(S,istart=itot,
                                                            rand=rand,
                                                            prewhiten=prewhiten,
                                                              name='s2')
                
                #indexing for knn is done using a different MCMC sample
                nbrs = NearestNeighbors(n_neighbors=kmax+1, 
                                    algorithm='auto',n_jobs=nproc).fit(samples2)
                k0=0 #k0 is the first knn
            else:
                #indexing for knn is done with the same MCMC samples
                k0=1 #avoid nn which is the point itself
                nbrs = NearestNeighbors(n_neighbors=kmax+1, 
                                    algorithm='auto',n_jobs=nproc).fit(samples)
                
            #compute knn distance. If indexed in same samples, DkNN(k=1)=0 
            DkNN, indices = nbrs.kneighbors(samples)                
    
            # Create the posterior for 'a' from the distances (volumes) to nearest neighbour:
            for k in range(k0,self.kmax):
                for j in range(0,S):        
                    # Use analytic formula for the volume of ndim-sphere:
                    volume[j,k] = math.pow(math.pi,ndim/2)*math.pow(DkNN[j,k],ndim)/sp.gamma(1+ndim/2)
                
                
                #print('volume minmax: ',volume[:,k].min(),volume[:,k].max())
                #print('weight minmax: ',weight.min(),weight.max())
                
                # dotp is the summation term in the notes:
                dotp = np.dot(volume[:,k]/weight[:],np.exp(fs))
        
                # The MAP value of 'a' is obtained analytically from the expression for the posterior:
                k_nn=k
                if k0==0:
                    k_nn=k+1
                amax = dotp/(S*k_nn+1.0)
    
                # Maximum likelihood estimator for the evidence
                SumW     = np.sum(self.gd.data['s1'].adjusted_weights)
                self.logger.debug('********sumW={0},np.sum(Weight)={1}'.format(SumW,np.sum(weight)))
                MLE[ipow,k] = math.log(SumW*amax*Jacobian) + logLmax - logPriorVolume

                self.logger.debug('SumW={} \t S={} '.format(SumW,S))
                self.logger.debug('amax={} \t Jacobian={}'.format(amax,Jacobian))
                self.logger.debug('logLmax={} \t logPriorVolume={}'.format(logLmax,logPriorVolume))
                self.logger.debug('MLE={}:'.format(MLE[ipow,k]))
                #print('---')
                # Output is: for each sample size (S), compute the evidence for kmax-1 different values of k.
                # Final columm gives the evidence in units of the analytic value.
                # The values for different k are clearly not independent. If ndim is large, k=1 does best.
                if self.brange is None:
                    #print('(mean,min,max) of LogLikelihood: ',fs.mean(),fs.min(),fs.max())
                    if verbose>1:
                        self.logger.debug('k={},nsample={}, dotp={}, median_volume={}, a_max={}, MLE={}'.format( 
                            k,S,dotp,statistics.median(volume[:,k]),amax,MLE[ipow,k]))
                
                else:
                    if verbose>1:
                        if ipow==0: 
                            self.logger.debug('(iter,mean,min,max) of LogLikelihood: ',ipow,fs.mean(),fs.min(),fs.max())
                            self.logger.debug('-------------------- useful intermediate parameter values ------- ')
                            self.logger.debug('nsample, dotp, median volume, amax, MLE')                
                        self.logger.debug(S,k,dotp,statistics.median(volume[:,k]),amax,MLE[ipow,k])

        #MLE[:,0] is zero - return only from k=1
        if self.brange is None:
            MLE=MLE[0,1:]
        else:
            MLE=MLE[:,1:]

        if verbose>0:
            for k in range(1,self.kmax):
                self.logger.info('   ln(B)[k={}] = {}'.format(k,MLE[k-1]))
            #print('')
        if info:
            return MLE, self.info
        else:  
            return MLE
        
#===============================================


def iscosmo_param(p,cosmo_params=None):
    '''
    check if parameter 'p' is cosmological or nuisance
    '''
    if cosmo_params is None:
        #list of cosmology parameters
        cosmo_params=['omegabh2','omegach2','theta','tau','omegak','mnu','meffsterile','w','wa',
                      'nnu','yhe','alpha1','deltazrei','Alens','Alensf','fdm','logA','ns','nrun',
                      'nrunrun','r','nt','ntrun','Aphiphi']        
    return p in cosmo_params

def params_info(fname,cosmo=False):
    '''
    Extract parameter names, ranges, and prior space volume
    from CosmoMC *.ranges file
    '''
    logger.info('getting params info from %s.ranges'%fname)
    par=np.genfromtxt(fname+'.ranges',dtype=None,names=('name','min','max'))#,unpack=True)
    parName=par['name']
    parMin=par['min']
    parMax=par['max']
    
    parMC={'name':[],'min':[],'max':[],'range':[]}
    for p,pmin,pmax in zip(parName, parMin,parMax):
        #if parameter info is to be computed only for cosmological parameters
        pcond=iscosmo_param(p) if cosmo else True 
        #now get info
        if not np.isclose(pmax,pmin) and pcond:
            parMC['name'].append(p)
            parMC['min'].append(pmin)
            parMC['max'].append(pmax)
            parMC['range'].append(np.abs(pmax-pmin))
    #
    parMC['str']=','.join(parMC['name'])
    parMC['ndim']=len(parMC['name'])
    parMC['volume']=np.array(parMC['range']).prod()
    
    return parMC

#==============================================

if __name__ == '__main__':

    print('---')
    #---------------------------------------
    #---- Extract command line arguments ---
    #---------------------------------------
    parser = ArgumentParser(prog=sys.argv[0],add_help=True,
                                description=desc,
                                epilog=cite)

    # positional args
    parser.add_argument("root_name",help='Root filename for MCMC chains or python class filename')
                        
    # optional args
    parser.add_argument("-k","--kmax",
                        dest="kmax",
                        default=2,
                        type=int,
                        help="scikit maximum K-NN ")
    parser.add_argument("-ic","--idchain",
                        dest="idchain",
                        default=0,
                        type=int,
                        help="Which chains to use - the id e.g 1 means read only *_1.txt (default=None - use all available) ")
    parser.add_argument("-np", "--ndim",
                        dest="ndim",
                        default=None,
                        type=int,                    
                        help="How many parameters to use (default=None - use all params) ")
    parser.add_argument("--burn","--burnlen",
                        dest="burnlen",
                        default=0,
                        type=float,                    
                        help="Burn-in length or fraction. burnlen<1 is interpreted as fraction e.g. 0.3 - 30%%")
    parser.add_argument("--thin", "--thinlen",
                        dest="thinlen",
                        default=0,
                        type=float,
                        help='''Thinning fraction. 
                             If 0<thinlen<1, MCMC weights are adjusted based on Poisson sampling
                             If thinlen>1, weighted thinning based on getdist algorithm 
                             If thinlen<0, thinning length will be the autocorrelation length of the chain
                             ''')
    parser.add_argument("-vb", "--verbose",
                        dest="verbose",
                        default=1,
                        type=int,
                        help="Increase output verbosity: 0: WARNNINGS, 1: INFO, 2: DEBUG, >2: EVERYTHING")

    parser.add_argument('--cosmo', help='flag to compute prior_volume using cosmological parameters only',
                        action='store_true')

    args = parser.parse_args()

    #-----------------------------
    #------ control parameters----
    #-----------------------------
    method=args.root_name
    kmax=args.kmax
    idchain=args.idchain 
    ndim=args.ndim
    burnlen=args.burnlen
    thinlen=args.thinlen
    verbose=args.verbose

    logger = logging.getLogger(__name__)
    
    if verbose>1:
        logger.setLevel(logging.DEBUG)
        
    try:
        parMC=params_info(method,cosmo=args.verbose)
        if verbose>1: print(parMC)
        prior_volume=parMC['volume']
        logger.info('getting prior volume using cosmomc *.ranges output')
        logger.info('prior_volume=%s'%prior_volume)
    except:
        raise
        #print('setting prior_volume=1')
        #prior_volume=1
    print()
    print('Using file: ',method)    
    mce=MCEvidence(method,ndim=ndim,priorvolume=prior_volume,idchain=idchain,
                                    kmax=kmax,verbose=verbose,burnlen=burnlen,
                                    thinlen=thinlen)
    mce.evidence()

    print('* ln(B)[k] is the natural logarithm of the Baysian evidence estimated using the kth Nearest Neighbour.')
    print('')
