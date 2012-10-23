
"""
A new approach to spectral clustering based on iterative eigen decomposition.
"""
import sys
import logging
import time
import scipy.sparse
import scipy.sparse.linalg
import numpy
import scipy.cluster.vq as vq

from exp.sandbox.EigenUpdater import EigenUpdater
from exp.sandbox.Nystrom import Nystrom
from apgl.data.Standardiser import Standardiser
from apgl.graph.GraphUtils import GraphUtils
from apgl.util.Parameter import Parameter
from apgl.util.ProfileUtils import ProfileUtils
from apgl.util.SparseUtils import SparseUtils
from apgl.util.VqUtils import VqUtils
from apgl.util.Util import Util

class IterativeSpectralClustering(object):
    def __init__(self, k1, k2, k3=100, nystromEigs=False):
        """
        Intialise this object with integer k1 which is the number of clusters to
        find, and k2 which is the maximum rank of the approximation of the shift
        Laplacian. When using the Nystrom approximation k3 is the number of row/cols
        used to find the approximate eigenvalues. 
        """
        Parameter.checkInt(k1, 1, float('inf'))
        Parameter.checkInt(k2, 1, float('inf'))
        Parameter.checkInt(k3, 1, float('inf'))
        Parameter.checkBoolean(nystromEigs)

        self.k1 = k1
        self.k2 = k2
        self.k3 = k3
        
        logging.debug("IterativeSpectralClustering(" + str((k1, k2, k3)) + ")")

        self.nb_iter_kmeans = 100
        self.nystromEigs = nystromEigs
        self.computeBound = False 

    def findCentroids(self, V, clusters):
        """
        Take an array of clusters and find the centroids using V.
        """
        k = numpy.unique(clusters).shape[0]
        centroids = numpy.zeros((k, V.shape[1]))

        for i in range(k):
            centroids[i, :] = numpy.mean(V[clusters==i, :], 0)
            
        return centroids 

    def clusterFromIterator(self, graphListIterator, approx=True, verbose=False, T=10):
        """
        Find a set of clusters for the graphs given by the iterator. If approx
        is True then we use the approximate eigen-update, otherwise we compute
        the whole eigen decomposition. If timeIter is true the each iteration is
        timed and the results are returned as a list.
        
        The difference between a weight matrix and the previous one should be
        positive.
        """
        Parameter.checkInt(T, 1, float('inf'))

        clustersList = []
        timeList = [] 
        boundList = []
        i = 0

        for subW in graphListIterator:
            if __debug__:
                Parameter.checkSymmetric(subW)

            logging.debug("Clustering graph of size " + str(subW.shape))
            startTime = time.time()
            ABBA = GraphUtils.shiftLaplacian(subW)

            # --- Eigen value decomposition ---
            if approx and i % T != 0:
                omega, Q = self.approxUpdateEig(subW, ABBA, omega, Q)   
                
                if self.computeBound:
                    inds = numpy.flipud(numpy.argsort(omega))
                    Q = Q[:, inds]
                    omega = omega[inds]
                    bounds = self.pertBound(omega, Q, omegaKbot, AKbot, self.k2)
                    #boundList.append([i, bounds[0], bounds[1]])
                    
                    #Now use accurate values of norm of R and delta   
                    rank = Util.rank(ABBA.todense())
                    gamma, U = scipy.sparse.linalg.eigsh(ABBA, rank-1, which="LM", ncv = ABBA.shape[0])
                    #logging.debug("gamma=" + str(gamma))
                    bounds2 = self.realBound(omega, Q, gamma, AKbot, self.k2)                  
                    boundList.append([i, bounds[0], bounds[1], bounds2[0], bounds2[1]])
            else:
                if approx and i != 0:
                    logging.info("Recomputing eigenvectors")

                if approx:                     
                    self.storeInformation(subW, ABBA)

                if not self.nystromEigs:
                    if self.computeBound: 
                        #omega, Q = scipy.sparse.linalg.eigsh(ABBA, min(self.k2*2, ABBA.shape[0]-1), which="LM", ncv = min(10*self.k2, ABBA.shape[0]))
                        rank = Util.rank(ABBA.todense())
                        omega, Q = scipy.sparse.linalg.eigsh(ABBA, rank-1, which="LM", ncv = ABBA.shape[0])
                        inds = numpy.flipud(numpy.argsort(omega))
                        omegaKbot = omega[inds[self.k2:]]  
                        QKbot = Q[:, inds[self.k2:]] 
                        AKbot = (QKbot*omegaKbot).dot(QKbot.T)
                        
                        omegaSort = numpy.flipud(numpy.sort(omega))
                    else: 
                        omega, Q = scipy.sparse.linalg.eigsh(ABBA, min(self.k2, ABBA.shape[0]-1), which="LM", ncv = min(10*self.k2, ABBA.shape[0]))
                else:
                    omega, Q = Nystrom.eigpsd(ABBA, self.k3)

            if approx:
                self.storeInformation(subW, ABBA)

            # --- Kmeans ---
            inds = numpy.flipud(numpy.argsort(omega))

            standardiser = Standardiser()
            #For some very strange reason we get an overflow when computing the
            #norm of the rows of Q even though its elements are bounded by 1.
            #We'll ignore it for now
            try:
                V = standardiser.normaliseArray(Q[:, inds[0:self.k1]].real.T).T
            except FloatingPointError as e:
                logging.warn("FloatingPointError: " + str(e))
            V = VqUtils.whiten(V)
            if i == 0:
                centroids, distortion = vq.kmeans(V, self.k1, iter=self.nb_iter_kmeans)
            else:
                centroids = self.findCentroids(V, clusters[:subW.shape[0]])
                if centroids.shape[0] < self.k1:
                    nb_missing_centroids = self.k1 - centroids.shape[0]
                    random_centroids = V[numpy.random.randint(0, V.shape[0], nb_missing_centroids),:]
                    centroids = numpy.vstack((centroids, random_centroids))
                centroids, distortion = vq.kmeans(V, centroids) #iter can only be 1
            clusters, distortion = vq.vq(V, centroids)

            clustersList.append(clusters)
            timeList.append(time.time()-startTime)


            #logging.debug("subW.shape: " + str(subW.shape))
            #logging.debug("len(clusters): " + str(len(clusters)))
            #from apgl.util.ProfileUtils import ProfileUtils
            #logging.debug("Total memory usage: " + str(ProfileUtils.memory()/10**6) + "MB")
            if ProfileUtils.memory() > 10**9:
                ProfileUtils.memDisplay(locals())

            i += 1

        if verbose:
            return clustersList, timeList, boundList
        else:
            return clustersList

    def approxUpdateEig(self, subW, ABBA, omega, Q):
        """
        Update the eigenvalue decomposition of ABBA
        """
        # --- remove rows/columns ---
        if self.n > ABBA.shape[0]:
            omega, Q = EigenUpdater.eigenRemove(omega, Q, ABBA.shape[0], min(self.k2, ABBA.shape[0]))

        # --- update existing nodes ---
        currentN = min(self.n, ABBA.shape[0])
        deltaDegrees = numpy.array(subW.sum(0)).ravel()[0:currentN]- self.degrees[:currentN]
        inds = numpy.arange(currentN)[deltaDegrees!=0]
        if len(inds) > 0:
            Y1 = ABBA[:currentN, inds] - self.ABBALast[:currentN, inds]
            Y1 = numpy.array(Y1.todense())
            Y1[inds, :] = Y1[inds, :]/2
            Y2 = numpy.zeros((currentN, inds.shape[0]))
            Y2[(inds, numpy.arange(inds.shape[0]))] = 1
            omega, Q = EigenUpdater.eigenAdd2(omega, Q, Y1, Y2, min(self.k2, currentN))

        # --- add rows/columns ---
        if self.n < ABBA.shape[0]:
            AB = numpy.array(ABBA[0:self.n, self.n:].todense())
            BB = numpy.array(ABBA[self.n:, self.n:].todense())
            omega, Q = EigenUpdater.lazyEigenConcatAsUpdate(omega, Q, AB, BB, min(self.k2, ABBA.shape[0]))
        
        return omega, Q
  
    def storeInformation(self, subW, ABBA):
        """
        Stored the current weight matrix degrees of subW, the shifted Laplacian
        ABBA. 
        """
        self.ABBALast = ABBA.copy()
        self.degrees = numpy.array(subW.sum(0)).ravel()
        self.n = ABBA.shape[0]


    def pertBound(self, pi, V, omegaKbot, AKbot, k): 
        """
        Bound the canonical angles using Frobenius and 2-norm, Theorem 4.4 in the paper. 
        """
        pi = numpy.flipud(numpy.sort(pi))
        
        #logging.debug("pi=" + str(pi))
        
        Vk = V[:, 0:k]
        normRF = numpy.linalg.norm(AKbot.dot(Vk), "fro")
        normR2 = numpy.linalg.norm(AKbot.dot(Vk), 2)
        delta = pi[k-1] - (pi[k] + omegaKbot[0])
        
        #logging.debug((normRF, normR2, delta))
        
        return normRF/delta,  normR2/delta
        
    def realBound(self, pi, V, gamma, AKbot, k): 
        """
        Compute the bound of the canonical angles using the real V and gamma 
        """
        pi = numpy.flipud(numpy.sort(pi))
        gamma = numpy.flipud(numpy.sort(gamma))
        
        Vk = V[:, 0:k]
        normRF = numpy.linalg.norm(AKbot.dot(Vk), "fro")
        normR2 = numpy.linalg.norm(AKbot.dot(Vk), 2)
        delta = pi[k-1] - gamma[k]
        
        #logging.debug((normRF, normR2, delta))
        
        
        return normRF/delta,  normR2/delta
        