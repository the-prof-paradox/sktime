# -*- coding: utf-8 -*-
"""KNN time series classification.

 Built on sklearn KNeighborsClassifier, this class supports a range of distance
 measure specifically for time series. These distance functions are defined in numba
 in sktime.distances. Python versions are in sktime.distances.elastic
 but these are orders of magnitude slower.

Please note that many aspects of this class are taken from scikit-learn's
KNeighborsTimeSeriesClassifier class with necessary changes to enable use with time
series classification data and distance measures.

todo: add a utility method to set keyword args for distance measure parameters.
(e.g.  handle the parameter name(s) that are passed as metric_params automatically,
depending on what distance measure is used in the classifier (e.g. know that it is w
for dtw, c for msm, etc.). Also allow long-format specification for
non-standard/user-defined measures e.g. set_distance_params(measure_type=None,
param_values_to_set=None,
param_names=None)
"""

__author__ = ["jasonlines", "TonyBagnall", "chrisholder"]
__all__ = ["KNeighborsTimeSeriesClassifier"]

from functools import partial

import numpy as np
from joblib import effective_n_jobs
from scipy import stats
from sklearn.metrics import pairwise_distances_chunked
from sklearn.neighbors import KNeighborsClassifier as _KNeighborsClassifier
from sklearn.neighbors._base import _check_weights, _get_weights
from sklearn.utils.extmath import weighted_mode
from sklearn.utils.multiclass import check_classification_targets
from sklearn.utils.validation import check_array

from sktime.classification.base import BaseClassifier

# New imports using Numba
from sktime.distances import distance_factory


class KNeighborsTimeSeriesClassifier(_KNeighborsClassifier, BaseClassifier):
    """KNN Time Series Classifier.

    An adapted version of the scikit-learn KNeighborsClassifier to work with
    time series data.

    Necessary changes required for time series data:
        -   calls to X.shape in kneighbors, predict and predict_proba.
            In the base class, these methods contain:
                n_samples, _ = X.shape
            This however assumes that data must be 2d (a set of multivariate
            time series is 3d). Therefore these methods
            needed to be overridden to change this call to the following to
            support 3d data:
                n_samples = X.shape[0]
        -   check array has been disabled. This method allows nd data via an
        argument in the method header. However, there
            seems to be no way to set this in the classifier and allow it to
            propagate down to the method. Therefore, this
            method has been temporarily disabled (and then re-enabled). It
            is unclear how to fix this issue without either
            writing a new classifier from scratch or changing the
            scikit-learn implementation. TO-DO: find permanent
            resolution to this issue (raise as an issue on sklearn GitHub?)


    Parameters
    ----------
    n_neighbors     : int, set k for knn (default =1)
    weights         : string or callable function, optional, default ==' uniform'
                      mechanism for weighting a vote, one of: 'uniform', 'distance'
                      or a callable function
    algorithm       : search method for neighbours {‘auto’, ‘ball_tree’,
    ‘kd_tree’, ‘brute’}: default = 'brute'
    distance          : distance measure for time series: {'dtw','ddtw',
    'wdtw','lcss','erp','msm','twe'}: default ='dtw'
    distance_params   : dictionary for metric parameters: default = None

    Examples
    --------
    >>> from sktime.classification.distance_based import KNeighborsTimeSeriesClassifier
    >>> from sktime.datasets import load_unit_test
    >>> X_train, y_train = load_unit_test(return_X_y=True, split="train")
    >>> X_test, y_test = load_unit_test(return_X_y=True, split="test")
    >>> classifier = KNeighborsTimeSeriesClassifier()
    >>> classifier.fit(X_train, y_train)
    KNeighborsTimeSeriesClassifier(...)
    >>> y_pred = classifier.predict(X_test)
    """

    _tags = {
        "capability:multivariate": True,
    }

    def __init__(
        self,
        n_neighbors=1,
        weights="uniform",
        distance="dtw",
        distance_params=None,
        **kwargs
    ):
        # self._distance_params = distance_params
        # if distance_params is None:
        #    self._distance_params = {}
        self.distance = distance
        self.distance_params = distance_params

        if isinstance(self.distance, str):
            distance = distance_factory(metric=self.distance)

        super(KNeighborsTimeSeriesClassifier, self).__init__(
            n_neighbors=n_neighbors,
            algorithm="brute",
            metric=distance,
            metric_params=None,  # Extra distance params handled in _fit
            **kwargs
        )
        BaseClassifier.__init__(self)
        self.weights = _check_weights(weights)

        # We need to add is-fitted state when inheriting from scikit-learn
        self._is_fitted = False

    def fit(self, X, y, **kwargs):
        """Override fit is required to sort out the multiple inheritance."""
        return BaseClassifier.fit(self, X, y, **kwargs)

    def _fit(self, X, y):
        """Fit the model using X as training data and y as target values.

        Parameters
        ----------
        X : sktime-format pandas dataframe with shape([n_cases,n_dimensions]),
        or numpy ndarray with shape([n_cases,n_readings,n_dimensions])

        y : {array-like, sparse matrix}
            Target values of shape = [n_samples]
        """
        # Transpose to work correctly with distance functions
        X = X.transpose((0, 2, 1))
        if isinstance(self.distance, str):
            if self.distance_params is None:
                self.metric = distance_factory(X[0], X[0], metric=self.distance)
            else:
                self.metric = distance_factory(
                    X[0], X[0], metric=self.distance, **self.distance_params
                )

        y = np.asarray(y)
        check_classification_targets(y)
        if y.ndim == 1 or y.ndim == 2 and y.shape[1] == 1:
            self.outputs_2d_ = False
            y = y.reshape((-1, 1))
        else:
            self.outputs_2d_ = True

        self.classes_ = []
        self._y = np.empty(y.shape, dtype=int)
        for k in range(self._y.shape[1]):
            classes, self._y[:, k] = np.unique(y[:, k], return_inverse=True)
            self.classes_.append(classes)

        if not self.outputs_2d_:
            self.classes_ = self.classes_[0]
            self._y = self._y.ravel()

        if hasattr(check_array, "__wrapped__"):
            temp = check_array.__wrapped__.__code__
            check_array.__wrapped__.__code__ = _check_array_ts.__code__
        else:
            temp = check_array.__code__
            check_array.__code__ = _check_array_ts.__code__
        #  this is not fx = self._fit(X, y) in order to maintain backward
        # compatibility with scikit learn 0.23, where _fit does not take an arg y
        fx = super()._fit(X)

        if hasattr(check_array, "__wrapped__"):
            check_array.__wrapped__.__code__ = temp
        else:
            check_array.__code__ = temp

        self._is_fitted = True
        return fx

    def _more_tags(self):
        """Remove the need to pass y with _fit.

        Overrides the scikit learn (>0.23) base class setting where 'requires_y' is true
        so we can call fx = self._fit(X) and maintain backward compatibility.
        """
        return {"requires_y": False}

    def kneighbors(self, X, n_neighbors=None, return_distance=True):
        """Find the K-neighbors of a point.

        Returns indices of and distances to the neighbors of each point.

        Parameters
        ----------
        X : sktime-format pandas dataframe with shape([n_cases,n_dimensions]),
        or numpy ndarray with shape([n_cases,n_readings,n_dimensions])
        y : {array-like, sparse matrix}
            Target values of shape = [n_samples]
        n_neighbors : int
            Number of neighbors to get (default is the value
            passed to the constructor).
        return_distance : boolean, optional. Defaults to True.
            If False, distances will not be returned

        Returns
        -------
        dist : array
            Array representing the lengths to points, only present if
            return_distance=True
        ind : array
            Indices of the nearest points in the population matrix.
        """
        self.check_is_fitted()
        # Transpose to work correctly with distance functions
        X = X.transpose((0, 2, 1))

        if n_neighbors is None:
            n_neighbors = self.n_neighbors
        elif n_neighbors <= 0:
            raise ValueError("Expected n_neighbors > 0. Got %d" % n_neighbors)
        else:
            if not np.issubdtype(type(n_neighbors), np.integer):
                raise TypeError(
                    "n_neighbors does not take %s value, "
                    "enter integer value" % type(n_neighbors)
                )

        if X is not None:
            query_is_train = False
            X = check_array(X, accept_sparse="csr", allow_nd=True)
        else:
            query_is_train = True
            X = self._fit_X
            # Include an extra neighbor to account for the sample itself being
            # returned, which is removed later
            n_neighbors += 1

        train_size = self._fit_X.shape[0]
        if n_neighbors > train_size:
            raise ValueError(
                "Expected n_neighbors <= n_samples, "
                " but n_samples = %d, n_neighbors = %d" % (train_size, n_neighbors)
            )
        n_samples = X.shape[0]
        sample_range = np.arange(n_samples)[:, None]

        n_jobs = effective_n_jobs(self.n_jobs)
        if self._fit_method == "brute":

            reduce_func = partial(
                self._kneighbors_reduce_func,
                n_neighbors=n_neighbors,
                return_distance=return_distance,
            )

            # for efficiency, use squared euclidean distances
            kwds = (
                {"squared": True}
                if self.effective_metric_ == "euclidean"
                else self.effective_metric_params_
            )

            result = pairwise_distances_chunked(
                X,
                self._fit_X,
                reduce_func=reduce_func,
                metric=self.effective_metric_,
                n_jobs=n_jobs,
                **kwds
            )
        else:
            raise ValueError("internal: _fit_method not recognized")

        if return_distance:
            dist, neigh_ind = zip(*result)
            result = np.vstack(dist), np.vstack(neigh_ind)
        else:
            result = np.vstack(result)

        if not query_is_train:
            return result
        else:
            # If the query data is the same as the indexed data, we would like
            # to ignore the first nearest neighbor of every sample, i.e
            # the sample itself.
            if return_distance:
                dist, neigh_ind = result
            else:
                neigh_ind = result

            sample_mask = neigh_ind != sample_range

            # Corner case: When the number of duplicates are more
            # than the number of neighbors, the first NN will not
            # be the sample, but a duplicate.
            # In that case mask the first duplicate.
            dup_gr_nbrs = np.all(sample_mask, axis=1)
            sample_mask[:, 0][dup_gr_nbrs] = False

            neigh_ind = np.reshape(neigh_ind[sample_mask], (n_samples, n_neighbors - 1))

            if return_distance:
                dist = np.reshape(dist[sample_mask], (n_samples, n_neighbors - 1))
                return dist, neigh_ind
            return neigh_ind

    def predict(self, X, **kwargs):
        """Predict wrapper."""
        return BaseClassifier.predict(self, X, **kwargs)

    def _predict(self, X):
        """Predict the class labels for the provided data.

        Parameters
        ----------
        X : sktime-format pandas dataframe or array-like, shape (n_query,
        n_features), or (n_query, n_indexed) if metric == 'precomputed' test samples.

        Returns
        -------
        y : array of shape [n_samples] or [n_samples, n_outputs]
            Class labels for each data sample.
        """
        self.check_is_fitted()

        if hasattr(check_array, "__wrapped__"):
            temp = check_array.__wrapped__.__code__
            check_array.__wrapped__.__code__ = _check_array_ts.__code__
        else:
            temp = check_array.__code__
            check_array.__code__ = _check_array_ts.__code__

        neigh_dist, neigh_ind = self.kneighbors(X)
        classes_ = self.classes_
        _y = self._y
        if not self.outputs_2d_:
            _y = self._y.reshape((-1, 1))
            classes_ = [self.classes_]

        n_outputs = len(classes_)
        n_samples = X.shape[0]
        weights = _get_weights(neigh_dist, self.weights)

        y_pred = np.empty((n_samples, n_outputs), dtype=classes_[0].dtype)
        for k, classes_k in enumerate(classes_):
            if weights is None:
                mode, _ = stats.mode(_y[neigh_ind, k], axis=1)
            else:
                mode, _ = weighted_mode(_y[neigh_ind, k], weights, axis=1)

            mode = np.asarray(mode.ravel(), dtype=np.intp)
            y_pred[:, k] = classes_k.take(mode)

        if not self.outputs_2d_:
            y_pred = y_pred.ravel()

        if hasattr(check_array, "__wrapped__"):
            check_array.__wrapped__.__code__ = temp
        else:
            check_array.__code__ = temp
        return y_pred

    def predict_proba(self, X, **kwargs):
        """Predict proba wrapper."""
        return BaseClassifier.predict_proba(self, X, **kwargs)

    def _predict_proba(self, X):
        """Return probability estimates for the test data X.

        Parameters
        ----------
        X : 3D numpy array dimensions (n,d,m) or (n_query, n_indexed) if metric ==
        'precomputed' Test samples.

        Returns
        -------
        p : array of shape = [n_samples, n_classes], or a list of n_outputs
            of such arrays if n_outputs > 1.
            The class probabilities of the input samples. Classes are ordered
            by lexicographic order.
        """
        self.check_is_fitted()

        if hasattr(check_array, "__wrapped__"):
            temp = check_array.__wrapped__.__code__
            check_array.__wrapped__.__code__ = _check_array_ts.__code__
        else:
            temp = check_array.__code__
            check_array.__code__ = _check_array_ts.__code__

        X = check_array(X, accept_sparse="csr")

        neigh_dist, neigh_ind = self.kneighbors(X)

        classes_ = self.classes_
        _y = self._y
        if not self.outputs_2d_:
            _y = self._y.reshape((-1, 1))
            classes_ = [self.classes_]

        n_samples = X.shape[0]

        weights = _get_weights(neigh_dist, self.weights)
        if weights is None:
            weights = np.ones_like(neigh_ind)

        all_rows = np.arange(X.shape[0])
        probabilities = []
        for k, classes_k in enumerate(classes_):
            pred_labels = _y[:, k][neigh_ind]
            proba_k = np.zeros((n_samples, classes_k.size))

            # a simple ':' index doesn't work right
            for i, idx in enumerate(pred_labels.T):  # loop is O(n_neighbors)
                proba_k[all_rows, idx] += weights[:, i]

            # normalize 'votes' into real [0,1] probabilities
            normalizer = proba_k.sum(axis=1)[:, np.newaxis]
            normalizer[normalizer == 0.0] = 1.0
            proba_k /= normalizer

            probabilities.append(proba_k)

        if not self.outputs_2d_:
            probabilities = probabilities[0]

        if hasattr(check_array, "__wrapped__"):
            check_array.__wrapped__.__code__ = temp
        else:
            check_array.__code__ = temp
        return probabilities


# overwrite sklearn internal checks, this is really hacky
# we now need to replace: check_array.__wrapped__.__code__ since it's
# wrapped by a future warning decorator
def _check_array_ts(array, *args, **kwargs):
    return array
