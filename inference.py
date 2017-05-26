#!/usr/bin/env python
import numpy as np
import numpy.random as rnd
import matplotlib.pyplot as plt
import pymc3 as pm
import pandas as pd
import theano as t
from theano import tensor as tt
from theano import printing as tp
from pymc3 import Model
from pymc3.distributions.continuous import Gamma,Beta
from pymc3.distributions.discrete import Categorical
from pymc3 import Deterministic
from pymc3.backends import Text

from data_generator import generate_data
from scipy import stats
import seaborn as sns
import scipy.optimize as opt

MAX_AXIS_CLUSTERS = 20
MAX_CLUSTERS = 40

def build_model(data,iter_count=1000,start=None):
    n,dim = data.shape
    bc_model = Model()
    with bc_model:
        axis_alpha = 1
        axis_beta = 1
        axis_dp_alpha = 2#Gamma("axis_dp_alpha",mu=2,sd=1)
        cluster_dp_alpha = 2#Gamma("cluster_dp_alpha",mu=2,sd=1)
        #concentration parameter for the clusters
        cluster_clustering = 500#Gamma("cluster_std_dev",mu=500,sd=250)
        cluster_sd = 0.2

        #per axis DP
        axis_betas = Beta("axis_betas",alpha=axis_alpha,beta=axis_beta,shape=(dim,MAX_AXIS_CLUSTERS))
        axis_cluster_magnitudes = tt.extra_ops.cumprod(1-axis_betas,axis=1)/(1-axis_betas)*axis_betas
        axis_cluster_magnitudes = tt.set_subtensor(
            axis_cluster_magnitudes[:,-1],
            1-tt.sum(axis_cluster_magnitudes[:,:-1],axis=1))
        axis_cluster_locations = Beta(
            "axis_cluster_locations",alpha=axis_alpha, beta=axis_beta, shape=(dim,MAX_AXIS_CLUSTERS))

        #second DP
        cluster_betas = Beta("cluster_betas",1,cluster_dp_alpha,shape=(MAX_CLUSTERS))
        cluster_magnitudes = tt.extra_ops.cumprod(1-cluster_betas)/(1-cluster_betas)*(cluster_betas)
        cluster_magnitudes = tt.set_subtensor(
            cluster_magnitudes[-1],
            1-tt.sum(cluster_magnitudes[:-1]))


        #spawn axis clusters
        cluster_locations = tt.zeros((MAX_CLUSTERS,dim))
        cluster_indicies = Categorical("cluster_indicies",shape=(MAX_CLUSTERS,dim),p=axis_cluster_magnitudes)
        for d in range(dim):
            #TODO:find a cleaner way of doing this
            cluster_locations = tt.set_subtensor(
                cluster_locations[:,d],
                axis_cluster_locations[d,cluster_indicies[:,d]])

        data_expectation = tt.zeros((n,dim))
        location_indicies = Categorical("location_indicies",shape=(n),p=cluster_magnitudes)
        for d in range(dim):
            data_expectation = tt.set_subtensor(
                data_expectation[:,d],
                cluster_locations[location_indicies,d])
        #x = Beta("data".format(d),shape=(n,d),mu=data_expectation,sd=cluster_std_dev,observed=data)

        #a=data_expectation*cluster_clustering
        #b=(1-data_expectation)*cluster_clustering
        #x = Beta("data",shape=(n,dim),alpha=a,beta=b,observed=data)

        x = Gamma("data",shape=(n,dim),mu=data_expectation,sd=cluster_sd,observed=data)
        db = Text('trace')

        #Log useful information
        Deterministic("cluster_locations",cluster_locations)
        Deterministic("cluster_magnitudes",cluster_magnitudes)
        Deterministic("logP",bc_model.logpt)
        
        #assign step methods for the sampler
        steps1 = pm.CategoricalGibbsMetropolis(vars=[location_indicies],proposal='uniform')
        steps2 = pm.CategoricalGibbsMetropolis(vars=[cluster_indicies],proposal='uniform')
        steps3 = pm.step_methods.HamiltonianMC(
            vars=[axis_betas,cluster_betas,axis_cluster_locations],step_scale=0.002,path_length=0.2)
        #steps3 = pm.step_methods.Metropolis(vars=[betas,betas2,axis_cluster_locations])
        trace = pm.sample(iter_count,start=start,init=None,tune=40000,n_init=10000, njobs=4,step=[steps1,steps2,steps3])

    return bc_model,trace

def plot_hard_clustering(model,trace,data,truth=None):
    #extract true indicies and extra indicies
    is_truth = truth is not None
    with model:
        map_index = np.argmax(trace["logP"])
        indicies = trace["location_indicies"][map_index]
        if is_truth:
            true_indicies = truth["location_indicies"]

    df = pd.DataFrame(data)

    dim = data.shape[1]

    def cluster_plot(x,y,**kwargs):
        sns.set_style('whitegrid')
        sns.plt.ylim(0,3)
        sns.plt.xlim(0,3)
        plt.scatter(x,y,**kwargs)

    print(indicies)
    df = df.assign(location_indicies = indicies)
    g = sns.PairGrid(df,hue="location_indicies",vars=range(dim))
    g.fig.suptitle('CLUSTERING')
    g.map_offdiag(cluster_plot)

    
    if is_truth:
        df = df.assign(location_indicies = true_indicies)
        h = sns.PairGrid(df,hue="location_indicies",vars=range(dim))
        h.fig.suptitle('GROUND TRUTH')
        h.map_offdiag(cluster_plot)

    plt.show()
    

def plot_ppd(model,trace,data):
    """Plot the posterior predictive distribution over the data.
    
    Takes N dimesensional data, a model, and a trace to produce 
    An N by N grid of 2d plots. Showing a scatter plot along each pair
    of axes.

    Args:
        model:the model of the data
        trace:trace generated by sampling from the model
        data:the ground truth data used to train the model
    Returns:
        None

    """
    n_predictions = 1000    
    burn_in = 500
    
    #generate array of predictions
    with model:
        samples = pm.sample_ppc(trace)
    predictions = samples["data"]
    predictions = predictions[burn_in:,:,:]
    t,n,d = predictions.shape
    predictions = np.reshape(predictions,(t*n,d))

    #grab a random sample of predictions
    np.random.shuffle(predictions)
    predictions = predictions[:n_predictions,:]

    def ppd_plot(x,y,**kwargs):
        """Plots kde if kwargs[source]="s" 
            or a scatter plot if kwargs[source]="o" """
        source = kwargs["source"]
        del kwargs["source"]
        sns.set_style('whitegrid')
        sns.plt.ylim(0,1)
        sns.plt.xlim(0,1)
        if source == "s":
            kwargs["cmap"] = "Oranges"
            sns.kdeplot(x,y,n_levels=20,**kwargs)
            #plt.scatter(x,y,**kwargs)
        elif source == "o":
            kwargs["cmap"] = "Blues"
            plt.scatter(x,y,**kwargs)
     
    df_predictive = pd.DataFrame(predictions)
    df_predictive = df_predictive.assign(source= lambda x: "s")
    df_observed = pd.DataFrame(data)
    df_observed = df_observed.assign(source= lambda x: "o")
    #merge observed and predicted data into one dataframe
    #and distinguishg them by the value of the "source" column
    df = pd.concat([df_predictive,df_observed],ignore_index=True)
    
    #Map ppd_plot onto the data in a pair grid to visualize predictive density 
    g = sns.PairGrid(df,hue="source",hue_order=["s","o"],hue_kws={"source":["s","o"]})
    g.map_offdiag(ppd_plot)
    plt.show()
    
    

def plot_max_n(trace,n,last,spacing):
    cl = trace["cluster_locations"]
    cm = trace["cluster_magnitudes"]
    print(cl)

    #find indicies of the largest clusters
    sort = np.argsort(cm,axis=1)
    #biggest cluster
    for i in range(n):
        indicies = sort[-last::spacing,i]
        values = cl[-last::spacing,indicies,:]
        sns.set_style('whitegrid')
        sns.plt.ylim(0,1)
        sns.plt.xlim(0,1)
        sns.kdeplot(values[:,0],values[:,0], bw='scott')

def main():
    print("START")
    data,state = generate_data()
    #model,trace = build_model(data,start=state)
    model,trace = build_model(data,start=None)
    #plot_ppd(model,trace,data)
    plot_hard_clustering(model,trace,data,state)
    print("DONE")

if __name__=="__main__":
    main()
