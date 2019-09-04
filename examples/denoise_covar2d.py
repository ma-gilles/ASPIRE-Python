"""
This script illustrates the covariance Wiener filtering functionality of the
ASPIRE, implemented by estimating the covariance of the unfiltered
images in a Fourier-Bessel basis and applying the Wiener filter induced by
that covariance matrix. The results can be reproduced exactly to the Matlab version
if the same methods of generating random numbers are used.
"""

import os
import logging

import numpy as np

from aspire.source import SourceFilter
from aspire.source.simulation import Simulation
from aspire.basis.ffb_2d import FFBBasis2D
from aspire.utils.filters import RadialCTFFilter
from aspire.utils.preprocess import downsample
from aspire.utils.coor_trans import qrand_rots
from aspire.utils.preprocess import vol2img
from aspire.utils.blk_diag_func import radial_filter2fb_mat
from aspire.image import im_filter_mat
from aspire.utils.matrix import anorm
from aspire.utils.matlab_compat import randn
from aspire.denoise.covar2d import RotCov2D
from aspire.denoise.covar2d_ctf import Cov2DCTF
from aspire.utils.blk_diag_func import blk_diag_add, blk_diag_mult, blk_diag_norm

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), '../tests/saved_test_data')


# Set the sizes of images 8 X 8
img_size = 8
# Set the total number of images generated from the 3D map
num_imgs = 1024

# Set the number of 3D maps
num_maps = 1

# Set the signal-noise ratio
sn_ratio = 1

# Specify the CTF parameters
pixel_size = 5                   # Pixel size of the images (in angstroms).
voltage = 200                    # Voltage (in KV)
defocus_min = 1.5e4              # Minimum defocus value (in angstroms).
defocus_max = 2.5e4              # Maximum defocus value (in angstroms).
defocus_ct = 7                   # Number of defocus groups.
Cs = 2.0                         # Spherical aberration
alpha = 0.1                      # Amplitude contrast

# Create a simulation object
sim = Simulation(
    n=num_imgs,
    C=num_maps,
    filters=SourceFilter([RadialCTFFilter(pixel_size, voltage,defocus=d, Cs=2.0, alpha=0.1)
                          for d in np.linspace(defocus_min, defocus_max, defocus_ct)], n=num_imgs)
)

# Load 3D map from the data file, corresponding to the experimentally obtained EM map of a 70S Ribosome.
vols = np.load(os.path.join(DATA_DIR, 'clean70SRibosome_vol.npy'))
vols = vols[..., np.newaxis]

# Downsample the 3D map to 8X8X8
vols = downsample(vols, (img_size*np.ones(3, dtype=int)))
sim.vols = vols

# Specify the fast FB basis method for expending the 2D images
ffbbasis = FFBBasis2D((img_size, img_size))

# Generate 2D clean images from input 3D map. The following statement can be used from the sim object:
# imgs_clean = sim.clean_images(start=0, num=num_imgs)
# To be consistent with the Matlab version in the numbers, we need to use the statements as below:
rots = qrand_rots(num_imgs, seed=0)
imgs_clean = vol2img(vols[..., 0], rots)

# Assign the CTF information and index for each image
h_idx = sim.filters.indices
filters = sim.filters.filters

# Evaluate CTF in the 8X8 coordinate grid
h_ctf = sim.filters.evaluate_grid(img_size)

# Evaluate CTF in the 8X8 FB basis
h_ctf_fb = [radial_filter2fb_mat(filt.evaluate_k, ffbbasis) for filt in filters]

# Apply the CTF to the clean images. For each defocus group, find the images that are assigned to that CTF,
# and filter them.
imgs_ctf_clean = np.zeros_like(imgs_clean)
for k in range(defocus_ct):
    mask = h_idx == k
    imgs_ctf_clean[..., mask] = im_filter_mat(imgs_clean[..., mask], h_ctf[..., k])
sim.cache(imgs_ctf_clean)

# Apply the noise at the desired singal-noise ratio to the filtered clean images
power_clean = anorm(imgs_ctf_clean)**2/np.size(imgs_ctf_clean)
noise_var = power_clean/sn_ratio
imgs_noise = imgs_ctf_clean + np.sqrt(noise_var)*randn(img_size, img_size, num_imgs, seed=0)

# Expand the images, both clean and noisy, in the Fourier-Bessel basis. This
# can be done exactly (that is, up to numerical precision) using the
# `basis.expand` function, but for our purposes, an approximation will do.
# Since the basis is close to orthonormal, we may approximate the exact
# expansion by applying the adjoint of the evaluation mapping using
# `basis.evaluate_t`.
coeff_clean = ffbbasis.evaluate_t(imgs_clean)
coeff_noise = ffbbasis.evaluate_t(imgs_noise)

# Create the Cov2D object and calculate mean and covariance for clean images without CTF.
# Given the clean Fourier-Bessel coefficients, we can estimate the symmetric
# mean and covariance. Note that these are not the same as the sample mean and
# covariance, since these functions use the rotational and reflectional
# symmetries of the distribution to constrain to further constrain the
# estimate. Note that the covariance matrix estimate is not a full matrix,
# but is block diagonal. This form is a consequence of the symmetry
# constraints, so to reduce space, only the diagonal blocks are stored. The
# mean and covariance estimates will allow us to evaluate the mean and
# covariance estimates from the filtered, noisy data, later.
cov2d = RotCov2D(sim, ffbbasis)
mean_coeff = cov2d.get_mean(coeff_clean)
covar_coeff = cov2d.get_covar(coeff_clean, mean_coeff)

# Create the Cov2DCFT object and estimate mean and covariance for noise images with CTF.
# We now estimate the mean and covariance from the Fourier-Bessel
# coefficients of the noisy, filtered images. These functions take into
# account the filters applied to each image to undo their effect on the
# estimates. For the covariance estimation, the additional information of
# the estimated mean and the variance of the noise are needed. Again, the
# covariance matrix estimate is provided in block diagonal form.
cov2dctf = Cov2DCTF(sim, ffbbasis)
mean_coeff_est = cov2dctf.get_mean_ctf(coeff_noise, h_ctf_fb, h_idx)
covar_coeff_est = cov2dctf.get_covar_ctf(coeff_noise, h_ctf_fb, h_idx, mean_coeff_est, noise_var=noise_var)

# Estimate the Fourier-Bessel coefficients of the underlying images using a
# Wiener filter. This Wiener filter is calculated from the estimated mean,
# covariance, and the variance of the noise. The resulting estimator has
# the lowest expected mean square error out of all linear estimators.
coeff_est = cov2dctf.get_cwf_coeffs(coeff_noise, h_ctf_fb, h_idx,
                                    mean_coeff=mean_coeff_est,
                                    covar_coeff=covar_coeff_est, noise_var=noise_var)

# Convert Fourier-Bessel coefficients back into 2D images
imgs_est = ffbbasis.evaluate(coeff_est)

# Evaluate the results
# Calculate the difference between the estimated covariance and the "true"
# covariance estimated from the clean Fourier-Bessel coefficients.
covar_coeff_diff = blk_diag_add(covar_coeff, blk_diag_mult(-1, covar_coeff_est))

# Calculate the deviation between the clean estimates and those obtained from
# the noisy, filtered images.
diff_mean = anorm(mean_coeff_est-mean_coeff)/anorm(mean_coeff)
diff_covar = blk_diag_norm(covar_coeff_diff)/blk_diag_norm(covar_coeff)

# Calculate the normalized RMSE of the estimated images.
nrmse_ims = anorm(imgs_est-imgs_clean)/anorm(imgs_clean)

print(f'Deviation of the noisy mean estimate: {diff_mean}')
print(f'Deviation of the noisy covariance estimate: {diff_covar}')
print(f'Estimated images normalized RMSE:{nrmse_ims}')

