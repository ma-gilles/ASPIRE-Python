import logging

import numpy as np

from aspire.noise import WhiteNoiseEstimator
from aspire.numeric import fft
from aspire.source import ImageSource
from aspire.utils.coor_trans import grid_2d

logger = logging.getLogger(__name__)


def adaptive_support(img_src, energy_threshold=0.99):
    """
    Determine size of the compact support in both real and Fourier Space.

    Returns c_limit (support radius in Fourier space),
    and R_limit (support radius in real space).

    Fourier c_limit is scaled in range [0, 0.5].
    R_limit is in pixels [0, Image.res/2].

    :param img_src: Input `Source` of images.
    :param energy_threshold: [0, 1] threshold limit
    :return: (c_limit, R_limit)
    """

    if not isinstance(img_src, ImageSource):
        raise RuntimeError("adaptive_support expects `Source` instance or subclass.")

    # Sanity Check Threshold is in range
    if energy_threshold <= 0 or energy_threshold > 1:
        raise ValueError(
            f"Given energy_threshold {energy_threshold} outside sane range [0,1]"
        )

    L = img_src.L
    N = L // 2

    r = grid_2d(L, shifted=False, normalized=False, dtype=img_src.dtype)["r"]

    # Estimate noise
    noise_est = WhiteNoiseEstimator(img_src)
    noise_var = noise_est.estimate()

    # Transform to Fourier space
    img = img_src.images(0, img_src.n).asnumpy()
    imgf = fft.centered_fft2(img)

    # Compute the Variance and Power Spectrum
    variance_map = np.mean(np.abs(img) ** 2, axis=0)
    pspec = np.mean(np.abs(imgf) ** 2, axis=0)

    # Compute the Radial Variance and Radial Power Spectrum
    radial_var = np.zeros(N)
    radial_pspec = np.zeros(N)
    for i in range(N):
        mask = (r >= i) & (r < i + 1)
        radial_var[i] = np.mean(variance_map[mask])
        radial_pspec[i] = np.mean(pspec[mask])

    # Subtract the noise variance
    radial_pspec -= noise_var
    radial_var -= noise_var

    # Lower bound variance and power by 0
    np.clip(radial_pspec, 0, a_max=None, out=radial_pspec)
    np.clip(radial_var, 0, a_max=None, out=radial_var)

    # Construct range of Fourier limits
    c = np.linspace(0, 0.5, N)
    # Construct range of Real limits
    R = np.arange(0, N, dtype=int)

    # Calculate cumulative energy
    cum_pspec = np.cumsum(radial_pspec * c)
    cum_var = np.cumsum(radial_var * R)

    # Normalize energies [0,1]
    cum_pspec /= cum_pspec[-1]
    cum_var /= cum_var[-1]

    # First note legacy code *=L for Fourier limit,
    #   but then only uses divided by L... so just removed here.
    #   This makes it consistent with Nyquist, ie [0, .5]
    # Second note, we attempt to find the cutoff,
    #   but when a search fails returns the last (-1) element,
    #   essentially the maximal radius.
    c_limit = c[np.argmax(cum_pspec > energy_threshold) or -1]
    R_limit = int(R[np.argmax(cum_var > energy_threshold) or -1])

    return c_limit, R_limit