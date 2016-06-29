# (C) William W. Cohen and Carnegie Mellon University, 2016
#
# miscellaneous matrix utilities
#

import scipy.sparse as SS
import scipy.io
import numpy as NP
import numpy.random as NR
import math
import logging

import config
import matrixdb

conf = config.Config()
conf.careful = True;       conf.help.careful = 'execute checks for matrix type and NANs'

# miscellaneous broadcast utilities used my ops.py and funs.py

NP.seterr(all='raise',under='ignore') 
# stop execution & print traceback for various floating-point issues
# except underflow; aiui we don't mind if very small numbers go to zero --kmm

def summary(mat):
    """Helpful string describing a matrix for debugging.""" 
    checkCSR(mat)
    return 'nnz %d rows %d cols %d' % (mat.nnz,numRows(mat),numCols(mat))

def pprintSummary(mat):
    if mat!=None:
        checkCSR(mat)
        return '%3d x %3d [%d nz]' % (numRows(mat),numCols(mat),mat.nnz)
    else:
        return '___'

def checkCSR(mat,context='unknown'):
    """Raise error if mat is not a scipy.sparse.csr_matrix."""
    if conf.careful:
        assert isinstance(mat,SS.csr_matrix),'bad type [context %s] for %r' % (context,mat)

def checkNoNANs(mat,context='unknown'):
    """Raise error if mat has nan's in it"""
    if conf.careful:
        checkCSR(mat)
        assert not NP.any(NP.isnan(mat.data)), 'nan\'s found: %s' % context

def maxValue(mat):
    try:
        return NP.max(mat.data)
    except ValueError:
        #zero-size array
        return -1

def densify(mat,maxExpansion=3):
    """Create a smallish dense version of a sparse matrix, which slices
    out the range of columns which have non-zero values, and return a pair
    D,I where D is the dense matrix, and I is information needed to
    invert the process for a matrix with the same dimensions.  Returns
    None if the dense matrix would be too much larger.
    """
    hiIndex = NP.max(mat.indices)
    loIndex = NP.min(mat.indices)
    denseSize = (hiIndex-loIndex) * numRows(mat)
    sparseSize = numRows(mat)+1 + 2*mat.nnz
    if denseSize>sparseSize*maxExpansion:
        return None,None
    else:
        newShape = (numRows(mat),hiIndex-loIndex+1)
        D = SS.csr_matrix((mat.data,mat.indices-loIndex,mat.indptr),shape=newShape,dtype='float64').todense()
        return D,(loIndex,numCols(mat))

def undensify(denseMat, info):
    (loIndex,numCols) = info
    (numRows,_) = denseMat.shape
    tmp = SS.csr_matrix(denseMat)
    result = SS.csr_matrix((tmp.data,tmp.indices+loIndex,tmp.indptr),shape=(numRows,numCols),dtype='float64')
    result.eliminate_zeros()
    return result

def mean(mat):
    """Return the average of the rows."""
    checkCSR(mat)
    #TODO - mat.mean returns a dense matrix which mutil converts, can I avoid that?
    #TODO - does this need broadcasting?
    return SS.csr_matrix(mat.mean(axis=0))

def mapData(dataFun,mat,selector=None,default=0):
    """Apply some function to the mat.data array of the sparse matrix and return a new one."""
    checkCSR(mat)
    def showMat(msg,m): print msg,type(m),m.shape
    dat = mat.data

    # FIXME: indptr isn't the same shape as indices! indptr maps
    # row->indices range, so if we mean to remove some of the things
    # in indices, indptr is going to get allllll messed up

    selected = None
    if selector: 
        selected = selector(mat.data)
        dat = dat[selected]
    newdata = dataFun(dat)
    if selector:
        buf = np.ones_like(mat.data) * default
        buf[selected] = newdata
        newdata = buf

    assert newdata.shape==mat.data.shape,'shape mismatch %r vs %r' % (newdata.shape,mat.data.shape)
    return SS.csr_matrix((newdata,mat.indices,mat.indptr), shape=mat.shape, dtype='float64')

#TODO get rid of this, it's expensive
def stack(mats):
    """Vertically stack matrices and return a sparse csr matrix."""
    for m in mats: checkCSR(m)
    return SS.csr_matrix(SS.vstack(mats, dtype='float64'))

def numRows(m): 
    """Number of rows in matrix"""
    checkCSR(m)
    return m.shape[0]

def numCols(m): 
    """Number of colunms in matrix"""
    checkCSR(m)
    return m.shape[1]

def nzCols(m,i):
    """Enumerate the non-zero columns in row i."""
    for j in range(m.indptr[i],m.indptr[i+1]):
        yield j

def repeat(row,n):
    """Construct an n-row matrix where each row is a copy of the given one."""
    checkCSR(row)
    d = NP.tile(row.data,n)
    inds = NP.tile(row.indices,n)
    assert numRows(row)==1
    numNZCols = row.indptr[1]
    ptrs = NP.array(range(0,numNZCols*n+1,numNZCols))
    return SS.csr_matrix((d,inds,ptrs),shape=(n,numCols(row)), dtype='float64')

def alterMatrixRows(mat,alterationFun):
    """ apply alterationFun(data,lo,hi) to each row.
    """
    for i in range(numRows(mat)):
        alterationFun(mat.data,mat.indptr[i],mat.indptr[i+1],mat.indices)

def softmax(db,mat):
    """ Compute the softmax of each row of a matrix.
    """
    nullEpsilon = -10  # scores for null entity will be exp(nullMatrix)
    result = repeat(db.nullMatrix(1)*nullEpsilon, numRows(mat)) + mat
    def softMaxAlteration(data,lo,hi,unused):
        rowMax = max(data[lo:hi])
        assert not math.isnan(rowMax)
        for j in range(lo,hi):
            data[j] = math.exp(data[j] - rowMax)
        rowNorm = sum(data[lo:hi])
        assert not math.isnan(rowNorm)
        for j in range(lo,hi):
            data[j] = data[j]/rowNorm
            assert not math.isnan(data[j])
            if data[j]==0:
                data[j] = math.exp(nullEpsilon)
    denseResult,undensifier = densify(result)
    if denseResult!=None:
        return undensify(denseSoftmax(denseResult), undensifier)
    else:
        alterMatrixRows(result,softMaxAlteration)
        return result

def denseSoftmax(m):
    #we want to make sure we keep the zero entries as zero
    mask = m!=0
    e_m = NP.multiply(NP.exp(m - m.max(axis=1)), mask)
    return e_m / e_m.sum(axis=1)

def broadcastAndComponentwiseMultiply(m1,m2):
    """ compute m1.multiply(m2), but broadcast m1 or m2 if necessary
    """
    checkCSR(m1); checkCSR(m2)
    def multiplyByBroadcastRowVec(m,v):
        #convert v to a dictionary
        vd = dict( (v.indices[j],v.data[j]) for j in range(v.indptr[0],v.indptr[1]) )
        def multiplyByVAlteration(data,lo,hi,indices):
            for j in range(lo,hi):
                data[j] *= vd.get(indices[j],0.0)
        result = m.copy()
        alterMatrixRows(result,multiplyByVAlteration)
        return result
    r1 = numRows(m1); r2 = numRows(m2)
    if r1==r2:
        return  m1.multiply(m2)
    else:
        assert r1==1 or r2==1, 'mismatched matrix sizes: #rows %d,%d' % (r1,r2)

    if r1==1:
        return multiplyByBroadcastRowVec(m1,m2)        
    elif r2==1:
        return multiplyByBroadcastRowVec(m1,m2)        
        
    return result

def broadcastAndWeightByRowSum(m1,m2):
    checkCSR(m1); checkCSR(m2)
    """ Optimized combination of broadcast2 and weightByRowSum operations
    """ 
    r1 = numRows(m1)
    r2 = numRows(m2)
    if r2==1:
        #DEBUG 
        try:
            return  m1 * m2.sum()
        except FloatingPointError:
            print "broadcastAndWeightByRowSum m1: %s" % summary(m1)
            print "broadcastAndWeightByRowSum m2.sum(): %s" % m2.sum()
            raise
    elif r1==1 and r2>1:
        n = numCols(m1)
        nnz1 = m1.data.shape[0]
        #allocate space for the broadcast version of m1,
        #with one copy of m1 for every row of m2
        data = NP.zeros(shape=(nnz1*r2,))
        indices = NP.zeros(shape=(nnz1*r2,),dtype='int')
        indptr = NP.zeros(shape=(r2+1,),dtype='int')
        ptr = 0
        indptr[0] = 0 
        for i in xrange(r2):
            w = m2.data[m2.indptr[i]:m2.indptr[i+1]].sum()
            #multiply the non-zero datapoints by w and copy them into the right places
            for j in xrange(nnz1):
                data[ptr+j] = m1.data[j]*w
                indices[ptr+j] = m1.indices[j]
            # increment the indptr so indptr[i]:indptr[i+1] tells
            # where to find the data, indices for row i
            indptr[i+1]= indptr[i]+nnz1
            ptr += nnz1
        result = SS.csr_matrix((data,indices,indptr),shape=(m2.shape[0],m2.shape[1]), dtype='float64')
        return result
    else:
        assert r1==r2
        result = m1.copy()
        for i in xrange(r1):
            w = m2.data[m2.indptr[i]:m2.indptr[i+1]].sum()
            result.data[result.indptr[i]:result.indptr[i+1]] *= w
        return result

def shuffleRows(m,shuffledRowNums=None):
    """Create a copy of m with the rows permuted."""
    checkCSR(m)
    if shuffledRowNums==None:
        shuffledRowNums = NP.arange(numRows(m))
        NR.shuffle(shuffledRowNums)
    data = NP.array(m.data)
    indices = NP.array(m.indices)
    indptr = NP.array(m.indptr)
    lo = 0
    for i in range(m.indptr.size-1 ):
        r = shuffledRowNums[i]
        rowLen = m.indptr[r+1] - m.indptr[r]
        indptr[i] = lo
        indptr[i+1] = lo + rowLen
        lo += rowLen
        for j in range(rowLen):
            data[indptr[i]+j] = m.data[m.indptr[r]+j]
            indices[indptr[i]+j] = m.indices[m.indptr[r]+j]
    result = SS.csr_matrix((data,indices,indptr), shape=m.shape, dtype='float64')
    result.sort_indices()
    return result    

def selectRows(m,lo,hi):
    """Return a sparse matrix that copies rows lo...hi-1 of m.  If hi is
    too large it will be adjusted. """
    checkCSR(m)
    if hi>numRows(m): hi=numRows(m)
    #data for rows [lo, hi) are in cells [jLo...jHi)
    jLo = m.indptr[lo]
    jHi = m.indptr[hi]  
    #allocate space
    data = NP.zeros(jHi - jLo)
    indices = NP.zeros(jHi - jLo, dtype='int')
    indptr = NP.zeros(hi - lo + 1, dtype='int')
    for i in range(hi - lo): 
        rowLen = m.indptr[lo+i+1] - m.indptr[lo+i]
        #translate the index pointers
        indptr[i] = m.indptr[lo+i] - jLo
        for j in range(rowLen):
            k = m.indptr[lo+i]+j
            data[indptr[i] + j] = m.data[k]
            indices[indptr[i] + j] = m.indices[k]
    indptr[hi-lo] = m.indptr[hi] - jLo
    result = SS.csr_matrix((data,indices,indptr), shape=(hi-lo,numCols(m)), dtype='float64')
    return result

if __name__=="__main__":
    tmp = []
    for i in range(1,11):
        tmp.append([i] + [0]*3 + [5*i])
    m = SS.csr_matrix(tmp)
    print m.todense()
    m2 = shuffleRows(m)
    #print m2.todense()
    for i in range(0,10,4):
        print selectRows(m2,i,i+4).todense()
