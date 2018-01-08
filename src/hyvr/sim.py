# -*- coding: utf-8 -*-
""" Hydrogeological Virtual Reality simulation package.

    Hydrogeological virtual reality (HYVR) simulator for object-based modelling of sedimentary structures

    Notes:
         Grid nodes are cell-centred!

"""
import os
import numpy as np
import pickle
import math
import time
import random
import scipy.io as sio
import matplotlib.pyplot as plt
import grid as gr
import utils as hu


def main(param_file):
    """ Main function for HYVR generation

    Args:
        param_file (str): Parameter file location

    Returns:

    """

    # Load parameter file
    run, model, sequences, hydraulics, flowtrans, elements, mg = hu.model_setup(param_file)

    for sim in range(1, int(run['numsim'])+1):
        # Generate facies
        props, params = facies(run, model, sequences, hydraulics, flowtrans, elements, mg)

        # Generate internal heterogeneity
        props, params = heterogeneity(props, params)

        # Save data
        if run['numsim'] > 1:
            realname = 'real_{:03d}'.format(sim)
            realdir = '{}\\{}\\'.format(run['rundir'], realname)
        else:
            realname = run['runname']
            realdir = run['rundir'] + '\\'

        hu.try_makefolder(realdir)

        if 'l_dataoutputs' in run:
            outdict = {'fac': props['fac'], 'mat': props['mat'], 'azim': props['azim'], 'dip': props['dip'],
                       'k_iso': props['k_iso'], 'poros': props['poros'], 'ae': props['ae_arr'],
                       'seq': props['seq_arr'], 'anirat': props['anirat'], 'ktensors': props['ktensors']}
            save_outputs(realdir, realname, run['l_dataoutputs'], mg, outdict)

        if 'l_modeloutputs' in run:
            save_models(realdir, realname, mg, run['l_modeloutputs'], flowtrans, props['k_iso'], props['ktensors'], props['poros'], props['anirat'])


def facies(run, model, sequences, hydraulics, flowtrans, elements, mg):
    """ Generate hydrofacies fields

    Args:
        param_file (str):   Parameter file location

    Returns:

    """

    """--------------------------------------------------------------------------------------------------------------
    Simulate sequence contacts
    --------------------------------------------------------------------------------------------------------------"""
    if len(sequences['l_seq']) > 1:
        """ Create contact surfaces """
        z_bot = np.zeros((mg.nx, mg.ny))
        seq_arr = np.zeros((mg.nx, mg.ny, mg.nz), dtype=np.int32)  # Initialise sequence storage array
        seq_top_z = np.zeros((mg.nx, mg.ny, len(sequences['l_seq'])))
        sequences['r_seq_bot'] = [0.0] * len(sequences['l_seq'])
        _, _, zzz = mg.meshup()

        for si, seqi in enumerate(sequences['l_seq']):
            if sequences['seq_contact'] == 'random' and si != len(sequences['l_seq']) - 1:
                sp = sequences['ll_seq_contact_model'][si]       # geostatistical parameters of sequence

                # Generate random top contact
                z_top = hu.specsim(mg, sp[0], [sp[1], sp[2]], twod=True, covmod='gau') + sequences['r_seq_top'][si]
                z_top = hu.round_x(z_top, base=model['dz'])           # round the values to grid resolution
            else:
                # Flat top contact
                z_top = np.ones((mg.nx, mg.ny)) * sequences['r_seq_top'][si]

            # Update lowest and highest values due to randomness
            sequences['r_seq_bot'][si] = np.max([np.mean(z_bot), mg.oz])
            sequences['r_seq_top'][si] = np.min([np.mean(z_top), mg.oz + mg.lz])

            # Assign z_bot and z_top values to entire array
            z_bot_arr = np.tile(z_bot[..., np.newaxis], [1, 1, mg.nz])
            z_top_arr = np.tile(z_top[..., np.newaxis], [1, 1, mg.nz])
            zae = np.logical_and(zzz >= z_bot_arr, zzz < z_top_arr)
            seq_arr[zae] = si

            z_bot = z_top                   # Update lower contact surface elevation
            seq_top_z[:, :, si] = z_top     # Assign sequence top to storage array

    else:
        # Only one sequence present
        seq_arr = np.zeros((mg.nx, mg.ny, mg.nz), dtype=np.int32)  # Initialise sequence storage array
        sequences['r_seq_bot'] = [mg.oz]
        sequences['r_seq_top'] = [mg.oz + mg.lz]

    """--------------------------------------------------------------------------------------------------------------
    Simulate architectural element units
    --------------------------------------------------------------------------------------------------------------"""
    if 'ae_table' in sequences:
        """ Load architectural element lookup table """
        ae_lu = hu.read_lu(sequences['ae_table'])

    elif len(sequences['ll_ae_z_mean']) < 0:
        """ Uniform Model """
        ae_lu = [[0, 0, mg.lz, sequences['ll_seq_ae'][si]]]
        ae_arr = np.ones((mg.nx, mg.ny, mg.nz), dtype=np.int32)  # Initialise sequence storage array

    else:
        """ Assign architectural element units """
        print(time.strftime("%d-%m %H:%M:%S", time.localtime(time.time())) + ': Generating architectural element unit contacts')

        # Initialise architectural element unit lookup table
        # [architectural element unit #, z_bottom, z_top, architectural element type, sequence #
        ae_lu = []
        count = 0

        for si, seqi in enumerate(sequences['l_seq']):
            # Randomly assign sequences / architectural element contact surfaces
            znow = sequences['r_seq_bot'][si]
            while znow < np.min([mg.lz, sequences['r_seq_top'][si]]):
                # Loop over all depths in sequence
                aelu_z = [count, znow, 0, 0, si]       # Initialise AE entry in lookup table (and assign identifier)

                # Assign architectural element
                aelu_z[3] = prob_choose(sequences['ll_seq_ae'][si],
                                        sequences['ll_ae_prob'][si])

                # Assign unit thickness
                ae_z_mean = sequences['ll_ae_z_mean'][si][sequences['ll_seq_ae'][si].index(aelu_z[3])]
                ae_z = hu.round_x(np.random.normal(ae_z_mean, ae_z_mean * 0.1), base=model['dz'])
                aelu_z[2] = min(ae_z + znow, mg.lz)

                # Assign avulsion
                avul_prob = np.array(sequences['ll_avul_prob'][si])
                yn = prob_choose([-1, 0], [avul_prob, 1 - avul_prob])         # Avulsion yes/no
                avudr = sequences['ll_avul'][si]      # Avulsion depth range for sequence
                dz = np.random.uniform(avudr[0], avudr[1]) * yn
                znow += ae_z + dz

                # Append to lookup table
                ae_lu.append(aelu_z)
                count += 1

    """ Create contact surfaces """
    z_bot = np.zeros((mg.nx, mg.ny))
    ae_arr = np.zeros((mg.nx, mg.ny, mg.nz), dtype=np.int32)  # Initialise sequence storage array
    _, _, zzz = mg.meshup()

    for ae_i, ae_z in enumerate(ae_lu):
        ae_dict = elements[ae_z[3]]                             # Get architectural element dict
        if ae_i == len(ae_lu)-1:
            # If AE unit is the upper-most in the domain
            z_top = np.ones((mg.nx, mg.ny)) * mg.lz             # Assign domain top as unit top
        elif ae_lu[ae_i+1][-1] != ae_z[-1]:
            # Use the sequence top contact if the AE unit is the top-most in the sequence
            z_top = seq_top_z[:, :, ae_z[-1]]
        elif ae_dict['contact'] == 'random':
            # Generate random top contact
            sp = ae_dict['r_contact_model']
            z_top = hu.specsim(mg, sp[0], [sp[1], sp[2]], twod=True, covmod='gau') + ae_z[2]
            z_top = hu.round_x(z_top, base=model['dz'])           # round the values to grid resolution
            ae_z[2] = np.mean(z_top)

        else:
            # Flat top contact
            z_top = np.ones((mg.nx, mg.ny)) * ae_z[2]

        # Assign z_bot and z_top values to entire array
        z_bot_arr = np.tile(z_bot[..., np.newaxis], [1, 1, mg.nz])
        z_top_arr = np.tile(z_top[..., np.newaxis], [1, 1, mg.nz])
        zae = np.logical_and(zzz >= z_bot_arr, zzz < z_top_arr)
        zae = np.logical_and(zae, seq_arr == ae_z[-1])
        ae_arr[zae] = ae_z[0]

        # Hack to make sure erosive elements aren't simulated in sequences below
        if ae_dict['geometry'] in ['trunc_ellip', 'channel']:
            ae_lu[ae_i][1] = np.max(z_bot.flatten())            # Update AE lookup table with highest value
        else:
            ae_lu[ae_i][1] = np.min(z_bot.flatten())            # Update AE lookup table with lowest value

        z_bot = z_top           # Update lower contact surface elevation

    # Save sequence lookup table
    # if 'ae_table' not in sequences:
    #     lu_savetxt = rundir + '/ae_lu_' + time.strftime('%d-%m-%Y_%H.%M.%S.txt')
    #     with open(lu_savetxt, 'w') as fwr:
    #         print('Sequences summary')
    #         for i in ae_lu:
    #             fwr.write('%s\n' % str()[1:-1])
    #             print(i)

    """--------------------------------------------------------------------------------------------------------------
    Hydrofacies simulation
    --------------------------------------------------------------------------------------------------------------"""
    # Initialise storage arrays
    count = 1
    mat, fac, azim, dip = save_arrays((mg.nx, mg.ny, mg.nz), bg=sequences['r_bg'], mat_count=count)

    """ Create architectural elements and associated hydrofacies fields """
    # Loop over AE units rather than elevations
    for ae_i in ae_lu:
        print(time.strftime("%d-%m %H:%M:%S", time.localtime(time.time())) + ': generating ' + ae_i[3] + ' from ' + str(ae_i[1]) + 'm')
        ae_dict = elements[ae_i[3]]

        # Assign background facies first
        # if 'r_bg' in ae_dict:
        #     aebg = int(ae_dict['r_bg'][0])
        # else:
        #     aebg = int(sequences['r_bg'][ae_i[4]])
        # fac[ae_arr == ae_i[0]] = aebg

        if ae_dict['geometry'] == 'trunc_ellip':
            # Generate truncated ellipsoid
            props_n, count = gen_trough(ae_dict, mg, model, ae_i, ae_arr, count)
            ae_mask = props_n['ae_arr_i'] == ae_i[0]

        elif ae_dict['geometry'] == 'channel':
            # Generate channel
            props_n, count = gen_channel(ae_dict, mg, model, ae_i, ae_arr, count)
            ae_mask = props_n['ae_arr_i'] == ae_i[0]

        elif ae_dict['geometry'] == 'sheet':
            # Generate sheet
            props_n, count = gen_sheet(ae_dict, mg, ae_i, ae_arr, count)
            ae_mask = ae_arr == ae_i[0]

        # Assign simulated values to storage arrays
        ae_arr[ae_mask] = ae_i[0]
        seq_arr[ae_mask] = ae_i[4]
        mat[ae_mask] = props_n['mat'][ae_mask]
        fac[ae_mask] = props_n['fac'][ae_mask]
        azim[ae_mask] = props_n['azim'][ae_mask]
        dip[ae_mask] = props_n['dip'][ae_mask]

    # Wrap storage arrays in a dictionary
    if run['flag_anisotropy']:
        props = [azim, mat, dip, fac, ae_arr, seq_arr]
    else:
        props = [mat, fac]
    params = [run, model, sequences, hydraulics, flowtrans, elements, mg, ae_lu]

    # Renumber material values from zero to remove eroded values
    mat = reindex(mat)

    return props, params


def heterogeneity(props, params):
    """ Generate internal heterogeneity

    Args:
        props:
        params:

    Returns:

    """
    print(time.strftime("%d-%m %H:%M:%S", time.localtime(time.time())) + ': generating hydraulic parameters')
    run, model, sequences, hydraulics, flowtrans, elements, mg, ae_lu = params
    azim, mat, dip, fac, ae_arr, seq_arr = props

    # Initialise storage arrays
    k_iso = np.zeros((mg.nx, mg.ny, mg.nz), dtype=np.float32)       # Horizontal hydraulic conductivity array
    poros = np.zeros((mg.nx, mg.ny, mg.nz), dtype=np.float32)       # Porosity array
    anirat = np.ones((mg.nx, mg.ny, mg.nz), dtype=np.float32)       # K_h/K_v anisotropy ratio

    if run['flag_het'] is True:
        # Heterogeneous case
        for mi in np.unique(mat):
            for fi in np.unique(fac[mat == mi]):
                mifi = (mat == mi) & (fac == fi)    # Get mask for relevant values

                if model['hetlev'] == 'internal':
                    # Generate internal heterogeneity
                    # Find outer limit of facies
                    fac_idx = np.where(mifi)                                # Get indices of facies
                    fac_nx = fac_idx[0].max() - fac_idx[0].min() + 1        # Get number of grid cells in x-direction
                    fac_ny = fac_idx[1].max() - fac_idx[1].min() + 1        # Get number of grid cells in y-direction
                    fac_nz = fac_idx[2].max() - fac_idx[2].min() + 1        # Get number of grid cells in z-direction

                    # Generate field with matching size
                    # Should include a condition that considers the characteristic lengths of the features
                    temp_gr = gr.Grid(dx=mg.dx, dy=mg.dy, dz=mg.dz, nx=fac_nx, ny=fac_ny, nz=fac_nz, gtype='cells')

                    # Generate internal heterogeneity - hydraulic conductivity
                    temp_k_small = hu.specsim(temp_gr, hydraulics['r_sig_y'][fi], hydraulics['ll_ycorlengths'][fi], covmod='exp')
                    temp_k_small = np.exp(temp_k_small) * hydraulics['r_k_h'][fi]          # back-transform from log space
                    temp_k = np.zeros((mg.nx, mg.ny, mg.nz), dtype=np.float32)

                    # Nest smaller array into larger array
                    # Get coordinates for 'nesting'
                    ix1 = fac_idx[0].min()
                    ix2 = ix1 + np.shape(temp_k_small)[0]
                    iy1 = fac_idx[1].min()
                    iy2 = iy1 + np.shape(temp_k_small)[1]
                    iz1 = fac_idx[2].min()
                    iz2 = iz1 + np.shape(temp_k_small)[2]

                    if np.shape(temp_k[ix1:ix2, iy1:iy2, iz1:iz2]) != np.shape(temp_k_small):
                        # QnD way to avoid indexing issues with nesting of the random field
                        iz1 -= 1
                        iz2 -= 1

                    # Insert into full-size array
                    temp_k[ix1:ix2, iy1:iy2, iz1:iz2] = temp_k_small
                    k_iso[mifi] = temp_k[mifi]

                    # Generate internal heterogeneity - porosity
                    temp_n_small = hu.specsim(temp_gr, hydraulics['r_sig_n'][fi], hydraulics['ll_ncorlengths'][fi], covmod='exp') + hydraulics['r_n'][fi]
                    temp_n = np.zeros((mg.nx, mg.ny, mg.nz), dtype=np.float32)
                    # Nest smaller array into larger array
                    temp_n[ix1:ix2, iy1:iy2, iz1:iz2] = temp_n_small
                    poros[mifi] = temp_n[mifi]

                elif model['hetlev'] == 'facies':
                    # Assign heterogeneity at facies level only
                    k_iso[mifi] = hydraulics['r_k_h'][fi]
                    poros[mifi] = hydraulics['r_n'][fi]

                # Assign anisotropy ratio
                anirat[mifi] = hydraulics['r_k_ratio'][fi]

        """ Assign background heterogeneity per architectural element """
        if model['hetlev'] == 'internal':
            for si, aei in enumerate(ae_lu):
                m0 = mat == 0
                ms = ae_arr == int(aei[0])
                aemask = m0 & ms     # Get material that equals zero within in architectural element
                if 'r_bg' in elements[aei[3]]:
                    aebackfac = int(elements[aei[3]]['r_bg'][0])   # architectural element background facies
                else:
                    aebackfac = int(sequences['r_bg'][0])

                # Assign background material
                temp_k = hu.specsim(mg, hydraulics['r_sig_y'][aebackfac], hydraulics['ll_ycorlengths'][aebackfac], covmod='exp')
                temp_k = np.exp(temp_k) * hydraulics['r_k_h'][aebackfac]          # back-transform from log space
                k_iso[aemask] = temp_k[aemask]

                # Generate internal heterogeneity - porosity
                temp_n = hu.specsim(mg, hydraulics['r_sig_n'][aebackfac], hydraulics['ll_ncorlengths'][aebackfac], covmod='exp')
                temp_n = temp_n + hydraulics['r_n'][aebackfac]
                poros[aemask] = temp_n[aemask]

                # Assign background anistropic ratio
                anirat[aemask] = hydraulics['r_k_ratio'][aebackfac]

        """ Assign trends to hydraulic parameters globally """
        if 'r_k_ztrend' in elements[aei[3]] or 'r_k_xtrend' in elements[aei[3]]:
            if 'k_trend' in hydraulics and hydraulics['k_trend'] == 'global':
                if 'r_k_ztrend' in hydraulics:
                    zf_vec = np.linspace(hydraulics['r_k_ztrend'][0], hydraulics['r_k_ztrend'][1], mg.nz)  # Z factor at each elevation
                else:
                    zf_vec = np.ones((mg.nx, mg.ny, mg.nz))

                if 'r_k_xtrend' in hydraulics:
                    xf_vec = np.linspace(hydraulics['r_k_xtrend'][0], hydraulics['r_k_xtrend'][1], mg.nx)
                else:
                    xf_vec = np.ones((mg.nx, mg.ny, mg.nz))

                _, xfmg, zfmg = np.meshgrid(np.arange(0, mg.ny), xf_vec,  zf_vec)

                k_iso *= xfmg * zfmg
            else:
                # Loop over assigned sequence architecutral elements
                for ei in elements:
                    if 'r_k_ztrend' in elements[aei[3]]:
                        zfactor_arr = np.ones((mg.nx, mg.ny, mg.nz)) * np.linspace(elements[aei[3]]['r_k_ztrend'][0], elements[aei[3]]['r_k_ztrend'][1], mg.nz)  # Z factor at each elevation
                    else:
                        zfactor_arr = np.ones((mg.nx, mg.ny, mg.nz))

                    if 'r_k_xtrend' in elements[aei[3]]:
                        xfactor_vec = np.linspace(elements[aei[3]]['r_k_xtrend'][0], elements[aei[3]]['r_k_xtrend'][1], mg.nx)  # X factor at each x-coordiante
                        xfactor_arr = np.ones((mg.nx, mg.ny, mg.nz)) * xfactor_vec[:, None, None]
                    else:
                        xfactor_arr = np.ones((mg.nx, mg.ny, mg.nz))

                    factor_arr = np.ones((mg.nx, mg.ny, mg.nz)) * zfactor_arr * xfactor_arr

                    for si, aei in enumerate(ae_lu):
                        if aei[3] == ei:
                            k_iso[ae_arr == aei[0]] *= factor_arr[ae_arr == aei[0]]

    else:
        # Homogeneous case
        for hy_idx, hyi in enumerate(hydraulics['l_hydro']):
            hyi = hy_idx
            k_iso[fac == hyi] = hydraulics['r_k_h'][hy_idx]
            poros[fac == hyi] = hydraulics['r_n'][hy_idx]
            anirat[fac == hyi] = hydraulics['r_k_ratio'][hy_idx]

    """ Assignment of anisotropy """
    # Initialise storage arrays
    ktensors = np.zeros((mg.nx, mg.ny, mg.nz, 3, 3), dtype=np.float32)

    # convert angles to radians
    azim = azim * np.pi/180
    dip = dip * np.pi/180

    # Create hydraulic conductivity tensors
    # kplane = anirat ** 0.5
    # kperp = 1 / anirat ** 0.5

    # T =========================
    # R = np.array([[np.cos(azim), np.sin(azim), 0],
    #                   [-np.sin(azim), np.cos(azim), 0],
    #                   [0, 0, 1]], dtype=np.float32) * \
    #         np.array([[np.cos(dip), 0, np.sin(dip)],
    #                   [0, 1, 0],
    #                   [-np.sin(dip), 0, np.cos(dip)]], dtype=np.float32)
    # /T =========================

    # Iterate over all nodes
    it = np.nditer(k_iso, flags=['multi_index'])
    while not it.finished:
        ii = it.multi_index
        xi, yi, zi = ii
        R = np.dot(np.array([[np.cos(azim[ii]), np.sin(azim[ii]), 0],
                            [-np.sin(azim[ii]), np.cos(azim[ii]), 0],
                            [0, 0, 1]], dtype=np.float32),
                   np.array([[np.cos(dip[ii]), 0, np.sin(dip[ii])],
                            [0, 1, 0],
                            [-np.sin(dip[ii]), 0, np.cos(dip[ii])]], dtype=np.float32))

        ktensors[xi, yi, zi, :, :] = k_iso[ii] * np.dot(R, np.dot(np.diag([1, 1, 1/anirat[ii]]), R.T))

        it.iternext()

    # convert radians to angles
    azim = azim * 180/np.pi
    dip = dip * 180/np.pi

    props = {'azim': azim, 'mat': mat, 'dip': dip, 'fac': fac, 'ae_arr': ae_arr, 'seq_arr': seq_arr,
             'k_iso': k_iso, 'ktensors': ktensors, 'poros': poros, 'anirat': anirat}

    if hydraulics:
        return props, params
    else:
        return props, params


def save_outputs(realdir, realname, outputs, mg, outdict):
    """ Save data arrays to standard formats

    Args:
        realdir     (str):  File path to save to
        realname    (str):  File name
        run
        mg
        outdict

    """

    print('Saving files in {}'.format(realdir))
    for output in outputs:
        if output == 'vtk':
            # VTK output for visualisation in ParaView
            hu.to_vtr({k: outdict[k] for k in outdict if k not in ['ktensors']}, realdir + realname, mg)

        if output == 'mat':
            # MATLAB output
            sio.savemat(realdir + realname + '.mat', outdict)

        if output == 'py':
            # Python pickle output
            with open(realdir + realname + '.dat', 'wb') as outfile:
                pickle.dump(outdict, outfile, protocol=pickle.HIGHEST_PROTOCOL)


def save_models(realdir, realname, mg, outputs, flowtrans, k_iso, ktensors, poros, anirat):
    """ Save HYVR outputs to standard modelling codes

    Args:
        run:
        mg:
        flowtrans:
        k_iso:
        ktensors:
        poros:
        anirat:

    """
    for output in outputs:
        if output == 'mf':
            # MODFLOW output

            # Create HGS output folder
            mfdir = realdir + 'MODFLOW\\'
            mfname = mfdir + realname
            hu.try_makefolder(mfdir)
            hu.to_modflow(mfname, mg, flowtrans, k_iso, anirat)

        if output == 'hgs':
            # HydroGeoSphere output
            # Create HGS output folder
            hgsdir = realdir + 'HGS\\'
            hu.try_makefolder(hgsdir)

            # Write to HGS files
            hu.to_hgs(hgsdir, mg, flowtrans, ktensors, poros)


"""-------------------------------------------------------------------------------------------------------------- """


"""--------------------------------------------------------------------------------------------------------------
Trough generators and utilities
--------------------------------------------------------------------------------------------------------------"""


def gen_trough(tr, mg, model, ae, ae_arr, count, ani=True):
    """ Create trough shapes.

    Args:
        tr      (dict):         trough parameters
        mg      (grid class):   model grid
        ae      (list):         architectural element unit details
        ae_arr  (ndarray):      3D array of sequeunce numbers
        count   (int):          material number
        ani     (bool):         Generate anisotropy?

    Returns:
        lpgrid: grid class of grid information
        lpdf:   leapfrog lithology as a pandas dataframe


    """

    x3, y3, z3 = mg.meshup()    # 3-D grid

    if ani:
        mat, fac, azim, dip = save_arrays((mg.nx, mg.ny, mg.nz), mat_count=count, bg=tr['r_bg'])
    else:
        mat, fac = save_arrays((mg.nx, mg.ny, mg.nz), mat_count=count, bg=tr['r_bg'], ani=False)
    count += 1

    ae_arr_i = np.zeros((mg.nx, mg.ny, mg.nz), dtype=int)

    # Assign background values
    ae_arr_i[ae_arr == ae[0]] = ae[0]

    # loop over trough top depths
    if 'buffer' in tr:
        tr_bot = ae[1] + tr['depth'] * tr['buffer']
    else:
        tr_bot = ae[1]
    tr_top = max(ae[2], tr_bot) + tr['agg']

    for znow in np.arange(tr_bot, tr_top, tr['agg']):
        for elno in np.arange(0, tr['el_z'] * mg.lx * mg.ly):
            # Reneration of trough parameters
            a, b, c = rand_trough(tr, mg=mg, ztr=znow)

            # center of trough
            if 'flag_display' in model and model['flag_display'] is True:
                # Place troughs in domain centre for display features
                xnow = mg.lx / 2
                ynow = 0
            elif 'r_migrate' in tr and znow > tr_bot:
                # Migration of troughs
                xnow += np.random.uniform(tr['r_migrate'][0], tr['r_migrate'][1])
                ynow += np.random.uniform(tr['r_migrate'][2], tr['r_migrate'][3])
            else:
                xnow = np.random.uniform(0, mg.lx)
                ynow = np.random.uniform(mg.ly/-2, mg.ly/2)

            alpha = np.random.uniform(tr['r_paleoflow'][0], tr['r_paleoflow'][1])   # orientation angle of trough ('paleoflow')
            angnow = np.random.uniform(tr['r_azimuth'][0], tr['r_azimuth'][1])      # orientation of material

            # Distances to ellipsoid centre
            xd = x3 - xnow
            yd = y3 - ynow
            zd = z3 - znow

            # Periodic boundary
            if model['flag_periodic'] is True:
                xd[xd > mg.lx / 2] -= mg.lx
                xd[xd < -mg.lx / 2] += mg.lx
                yd[yd > mg.ly / 2] -= mg.ly
                yd[yd < -mg.ly / 2] += mg.ly
                zd[zd > mg.lz / 2] -= mg.lz
                zd[zd < -mg.lz / 2] += mg.lz

            # scaled and rotated distance squared
            select, R2 = scale_rotate(xd, yd, zd, alpha, a, b, c)
            select = np.logical_and(select, ae_arr <= ae[0])                # Restrict selection to AE units equal or below current


            """" Assign internal structure """
            tr_struct = tr['structure']
            if tr_struct == 'random':
                tr_struct = random.choice(['dip', 'bulb_l'])

            if np.all(np.isnan(select)):
                # Skip section if no grid cells selected
                pass
            if ~np.any(select):
                # Skip section if no grid cells selected
                pass

            if model['hetlev'] == 'ae':
                # Add 'dip layers' into trough
                fac_now = random.choice(tr['l_facies'])
                fac[select] = fac_now
                mat[select] = count
                if ani:
                    azim[select] = angnow    # Save angle
                    dip[select] = np.random.uniform(tr['r_dip'][0], tr['r_dip'][1])                    # Assignment of architectural elements only
            elif tr_struct == 'bulb':
                """
                Add 'bulb' layers into trough
                    - Dip is derived from the gradient of the truncated ellipsoid boundary
                    - Azimuth is the angle of the ellipsoid
                """
                # Generate gradient information
                dip_tr, azim_tr = ellipsoid_gradient(xd, yd, zd, a, b, c, alpha, select, tr)

                # Assign generated values to grid cells
                fac_now = random.choice(tr['l_facies'])
                fac[select] = fac_now
                mat[select] = count
                if ani:
                    dip[select] = dip_tr[select]
                    azim[select] = azim_tr[select]

            elif tr_struct == 'bulb_l':

                # Ellipsoid 'c' radii
                c_range = c - np.arange(0, c, tr['bulbset_d'])

                # Iterate over internal truncated ellipsoids
                for c_idx, c_now in enumerate(c_range):
                    # Get scale factor for ellipsoids -
                    te_scale = c_now / c
                    a_now, b_now = np.array([a, b]) * te_scale

                    # Internal scaled and rotated distance squared
                    bulb_select, bulb_R2 = scale_rotate(xd, yd, zd, alpha, a_now, b_now, c_now)

                    # Generate gradient information
                    dip_bulb, azim_bulb = ellipsoid_gradient(xd, yd, zd, a_now, b_now, c_now, alpha, bulb_select, tr)

                    # Assign generated values to grid cells
                    if c_idx == 0:
                        # Randomly choose facies
                        fac_now = random.choice(tr['l_facies'])
                    else:
                        # Choose next hydrofacies from alternating sets
                        pf_i = [i for i, x in enumerate(tr['l_facies']) if x == str(fac_now)][0]    # Get facies index
                        fac_now = random.choice(tr['ll_altfacies'][pf_i])                   # Get next alternating facies

                    fac[bulb_select] = fac_now                                      # Alternating facies
                    mat[bulb_select] = count

                    if ani:
                        dip[bulb_select] = dip_bulb[bulb_select]
                        azim[bulb_select] = azim_bulb[bulb_select]

            elif tr_struct == 'dip':
                # Add 'dip layers' into trough, with alternating facies
                do, fd, dv, av = dip_sets(mg, tr, znow, select=select, azimuth_z=alpha)

                # Assign generated values to grid cells
                fac[select] = fd[select]
                mat[select] = count
                if ani:
                    dip[select] = dv
                    azim[select] = av

            else:
                # Add 'dip layers' into trough
                fac[select] = random.choice(tr['l_facies'])
                mat[select] = count
                if ani:
                    azim[select] = angnow    # Save angle
                    dip[select] = np.random.uniform(tr['r_dip'][0], tr['r_dip'][1])
            count += 1
            ae_arr_i[select] = ae[0]

    if ani:
        props = {'mat': mat, 'azim': azim, 'dip': dip, 'fac': fac, 'ae_arr_i': ae_arr_i}
    else:
        props = {'mat': mat, 'fac': fac, 'ae_arr_i': ae_arr_i}

    return props, count


def scale_rotate(x, y, z, alpha=0, a=1, b=1, c=1):
    """
    Scale and rotate three-dimensional trough

    :Parameters:
        x, y, z: float
            spatial coordinates
        alpha: float
            rotation angle about the z-axis
        a, b, c: float
            axis lengths in x, y, z directions (ellipsoid length, width, depth)
    :return:
        select: grid cells within ellipsoid
        R2:     grid of scaled and rotated values

    : Authors:
        Jeremy Bennett
    """

    alpha = np.radians(alpha)
    R2 = (x ** 2 * np.cos(alpha) ** 2 +
          2 * x * y * np.cos(alpha) * np.sin(alpha) +
          y ** 2 * np.sin(alpha) ** 2) / a ** 2 + \
         (x ** 2 * np.sin(alpha) ** 2 -
          2 * np.multiply(x, y) * np.cos(alpha) * np.sin(alpha) +
          np.power(y, 2) * np.cos(alpha) ** 2) / b ** 2 + \
          z ** 2 / c ** 2

    #  selection of cells
    mask1 = R2 <= 1
    mask2 = z <= 0
    select = mask1 & mask2

    return select, R2


def ellipsoid_gradient(x, y, z, a, b, c, alpha, select, tr):
    """
    Calculate dip and strike values in rotated ellipsoids

    Args:
        x, y, z:    distances to centre of ellipsoid
        a, b, c:    majox/minor axes of ellipsoid
        alpha:      rotation of ellipsoid from mean flow direction

    Returns:
        dip:
        strike:

    """

    alpha = np.radians(-alpha)  # Convert alpha to radians

    # initialize arrays
    dip_g = np.zeros(np.shape(x))
    azimuth_g = np.zeros(np.shape(x))

    try:
        idx_z = np.where(select)[2].max()     # Find the 'surface cells' of the ellipsoid
    except ValueError:
        # Return if no values selected
        return dip_g, azimuth_g

    # Calcuate dip and strike for onion
    select_z_idx = np.where(select[:, :, idx_z])                    # Indices of grid cells in unit at top of unit
    ix = x[select_z_idx[0], select_z_idx[1], idx_z].flatten()
    iy = y[select_z_idx[0], select_z_idx[1], idx_z].flatten()
    iz = (1 - ((ix * np.cos(alpha) + iy * np.sin(alpha)) ** 2 / a ** 2 + (ix * np.sin(alpha) + iy * np.cos(alpha)) ** 2 / b ** 2) ** 0.5) * c

    # Get tangent plane coefficients
    fx = (2 * np.cos(alpha) ** 2 * ix + 2 * iy * np.cos(alpha) * np.sin(alpha)) / a ** 2 \
        + (2 * np.sin(alpha) ** 2 * ix + 2 * iy * np.cos(alpha) * np.sin(alpha)) / b ** 2
    fy = (2 * np.sin(alpha) ** 2 * iy + 2 * ix * np.cos(alpha) * np.sin(alpha)) / a ** 2 \
        + (2 * np.cos(alpha) ** 2 * iy + 2 * ix * np.cos(alpha) * np.sin(alpha)) / b ** 2
    fz = 2 * iz / c

    # Normal vectors of tangent plane, horizontal plane, vertical plane
    n_tan = np.array([fx, fy, fz]).T
    n_horizontal = np.array([0., 0., 1.])

    # Calculate the dip at each point
    dip_vec = np.minimum(tr['r_dip'][1], (angle(n_tan, n_horizontal) * 180/np.pi))

    # Insert into 2D array
    dip2d = np.zeros(np.shape(x)[0:2])
    dip2d[select_z_idx[0], select_z_idx[1]] = dip_vec
    dip2d = dip2d[:, :, None] * np.ones(np.shape(x))

    # Apply to 3D arrays
    dip_g[select] = dip2d[select]
    azimuth_g[select] = alpha * 180/np.pi

    return dip_g, azimuth_g


def rand_trough(tr, mg=False, ztr=[]):
    """
    Randomly generate ellipsoid geometry parameters:

    Args:
        tr:     ellipsoid parameters
        mg:     Meshgrid object
        ztr:    elevation of ellipsoid centre point

    """
    if ztr and 'r_geo_ztrend' in tr:
        zfactor = np.interp(ztr, [mg.oz, mg.oz + mg.lz], [tr['r_geo_ztrend'][0], tr['r_geo_ztrend'][1]])
    else:
        zfactor = 1

    a = tr['length'] * zfactor / 2
    b = tr['width'] * zfactor / 2
    c = tr['depth'] * zfactor

    return a, b, c

"""--------------------------------------------------------------------------------------------------------------
Channel generators and utilities
--------------------------------------------------------------------------------------------------------------"""


def gen_channel(ch_par, mg, model, seq, ae_array, count, ani=True):
    """
    Generate channels architectural element:
        - Flow regime is assumed to be reasonably constant so the major geometry of the channels doesn't change so much
        - 'Migration' of the channels according to a shift vector

    Args:
        ch_par:         channel parameters
        mg:         model grid class
        z_in:       starting depth
        thickness:  thickness of architectural element

    Returns:

    """

    # Vectors of spatial coordinates
    xvec, yvec, zvec = mg.vec()
    x2, y2 = np.meshgrid(xvec, yvec, indexing='ij')          # 2-D grid
    _, _, z3 = np.meshgrid(range(0, mg.nx), range(0, mg.ny), range(0, mg.nz), indexing='ij')          # 2-D grid

    # Initialize storage arrays
    if ani:
         mat, fac, azim, dip = save_arrays((mg.nx, mg.ny, mg.nz), bg=ch_par['r_bg'], mat_count=count)
    else:
        mat, fac = save_arrays((mg.nx, mg.ny, mg.nz), bg=ch_par['r_bg'], mat_count=count, ani=False)

    ae_arr_i = np.zeros(np.shape(ae_array), dtype=int)
    ae_arr_i[ae_array == seq[0]] = seq[0]

    # start location
    total_channels = int(ch_par['channel_no'])
    if 'flag_display' in model and model['flag_display'] is True:
        # Place troughs in domain centre for display features
        xstart = np.random.uniform(0, 0, total_channels)
        ystart = np.random.uniform(yvec[0], yvec[-1], total_channels)
    else:
        # Randomly place channel starting points
        xstart = np.random.uniform(-10, 0, total_channels)
        ystart = np.random.uniform(yvec[0], yvec[-1], total_channels)

    # loop over channel top depths
    if 'buffer' in ch_par:
        ch_bot = seq[1] + ch_par['depth'] * ch_par['buffer']
    else:
        ch_bot = seq[1]
    ch_top = seq[2] + ch_par['r_mig'][2]

    for znow in np.arange(ch_bot, ch_top, ch_par['r_mig'][2]):
        print(time.strftime("%d-%m %H:%M:%S", time.localtime(time.time())) + ' z = ' + str(znow))
        # Assign linear trend to channel sizes
        if 'r_geo_ztrend' in ch_par:
            zfactor = np.interp(znow, [mg.oz, mg.oz + mg.lz], [ch_par['r_geo_ztrend'][0], ch_par['r_geo_ztrend'][1]])
        else:
            zfactor = 1
        z_ch_width = ch_par['width'] * zfactor
        z_ch_depth = ch_par['depth'] * zfactor

        # Loop over total channels per sequence
        for chan in range(0, total_channels):
            """ Loop over multiple channels at 'timestep' """
            aha = ferguson_channel(mg, ch_par['h'], ch_par['k'],  ch_par['ds'], ch_par['eps_factor'], disp=model['flag_display'])

            """ Get flow direction in channel for azimuth """
            # For periodicity shift trajectories into model unit cell
            if model['flag_periodic'] is True:
                aha[aha[:, 1] < yvec[0], 1] += mg.ly
                aha[aha[:, 1] > yvec[-1], 1] -= mg.ly

            # initialize 2-D distance matrix
            D = 1e20 * np.ones_like(x2)

            # initialize sum of inverse-distance weights
            sumW = np.zeros(np.shape(x2), dtype=float)
            W = np.zeros(np.shape(x2), dtype=float)

            # initialize velocity orientation at this level
            vx_znow = np.zeros(np.shape(x2), dtype=float)
            vy_znow = np.zeros(np.shape(x2), dtype=float)

            # loop over all points of trajectory
            for ii in range(0, len(aha)):
                # distance to current point
                R = np.sqrt((x2 - aha[ii][0]) ** 2 + (y2 - aha[ii][1]) ** 2)

                # smallest distance of entire grid to all points so far
                D[R < D] = R[R < D]

                # inverse-distance weight for velocity interpolation
                W[:] = 1e-20
                W[R < z_ch_width / 2] = 1 / (R[R < z_ch_width / 2] + 1e-20)

                # velocity interpolation in 2-D
                vx_znow += aha[ii][2] * W
                vy_znow += aha[ii][3] * W
                sumW += W

            vx_znow /= sumW
            vy_znow /= sumW

            # Assign facies sets with dip values
            if sum(ch_par['r_dip']) > 0:
                do, fd, dv, av = dip_sets(mg, ch_par, znow, channel=[aha[:, 0], aha[:, 1], vx_znow, vy_znow])
            else:
                do = np.ones((mg.nx, mg.ny, mg.nz)) + count
                fd = np.ones((mg.nx, mg.ny, mg.nz), dtype=int) * int(random.choice(ch_par['l_facies']))
                dv = 0.0
                av = np.zeros((mg.nx, mg.ny, mg.nz))

            """ Copy results into 3-D field """
            # Iterate over all nodes below current top elevation
            d_range = np.arange(max(0, mg.idx_z(znow - z_ch_depth)), min(mg.nz, mg.idx_z(znow)))      # Depth range
            if len(d_range) > 0:        # Only compute if channel depth range is finite

                # Get mask arrays for each condition
                in_channel = D[:, :, None]**2 <= z_ch_width**2 / 4 - ((mg.idx_z(znow) - z3) * mg.dz * z_ch_width / (z_ch_depth*2)) ** 2     # is grid cell in channel
                finite_v = ~np.isnan(vx_znow)            # Only assign if velocity is finite
                below_top = ae_array <= seq[0]            # Don't assign values to locations higher than top contact surface
                chan_mask = in_channel * finite_v[:, :, None] * below_top

                # Assign properties
                fac[chan_mask] = fd[chan_mask]
                mat[chan_mask] = count
                ae_arr_i[chan_mask] = seq[0]

                if 'l_lag' in ch_par:
                    in_lag = (znow - z_ch_depth + float(ch_par['l_lag'][0])) > z3 * mg.dz   # Is grid cell in channel
                    fac[np.logical_and(in_channel, in_lag)] = int(ch_par['l_lag'][1])

                if ani:
                    # calcuate azimuth, to 1 degree
                    azim2d = np.round((np.arctan2(vx_znow, vy_znow) - np.pi/2) * 180/np.pi)
                    azim3d = azim2d[:, :, None] * np.ones((mg.nx, mg.ny, mg.nz))
                    azim[chan_mask] = azim3d[chan_mask]
                    dip[chan_mask] = dv

                count += 1

        # Shift starting values with migration vector from parameter file
        xstart += ch_par['r_mig'][0]
        ystart += ch_par['r_mig'][1]
    if ani:
        props = {'mat': mat, 'azim': azim, 'dip': dip, 'fac': fac, 'ae_arr_i': ae_arr_i}
    else:
        props = {'mat': mat, 'fac': fac, 'ae_arr_i': ae_arr_i}
    return props, count


def ferguson_channel(mg, h, k, ds, eps_factor, dist=0, disp=False):
    """
    Simulate channel centrelines using the Ferguson (1976) disturbed meander model
    Implementation of AR2 autoregressive model
    http://onlinelibrary.wiley.com/doi/10.1002/esp.3290010403/full

    Args:
        mg:
        h:
        k:
        ds:
        eps_factor:
        dist (float): Distance to generate channels - defaults to mg.lx
        disp (bool): Creating display channel - channel begins at (0,0)

    Return

    """
    # Parameters
    ds += 1e-10
    if dist > 0:
        ns = dist
    else:
        ns = mg.lx * 2
    s = np.arange(0, ns, ds)

    # Centreline starting point
    xp = 0
    yp = 0

    # Calculate channel directions
    theta = ferguson_theta(s, eps_factor, k, h)

    # Interpolate channel direction over interval of interest
    s_interp, th_interp = curve_interp(s, theta, 0.1)

    # Storage array
    outputs = np.zeros((len(th_interp), 4))

    for th_idx, th_i in enumerate(th_interp):
        vx = ds*np.cos(th_i)
        vy = ds*np.sin(th_i)
        xp += vx
        yp += vy

        # Assign to storage array
        outputs[th_idx, 0] = xp       # x coordinate
        outputs[th_idx, 1] = yp       # y coordinate
        outputs[th_idx, 2] = vx       # vx
        outputs[th_idx, 3] = vy       # vy

    # Rotate meanders into mean flow direction
    mean_th = -np.mean(th_interp)
    rotMatrix = np.array([[np.cos(mean_th), -np.sin(mean_th)],
                          [np.sin(mean_th),  np.cos(mean_th)]])
    roro = np.dot(rotMatrix, outputs[:, 0:2].transpose())

    outputs[:, 2:] = np.dot(rotMatrix, outputs[:, 2:].transpose()).transpose()

    # Move to random starting location in x-direction
    outputs[:, 0] = roro[0, :].transpose() + np.random.uniform(-50, -10)
    outputs[:, 1] = roro[1, :].transpose()

    # Remove values before model domain
    if dist > 0:
        indomain = outputs[:, 0] >= mg.ox
    else:
        indomain = np.logical_and(outputs[:, 0] >= mg.ox, outputs[:, 0] <= mg.nx)
    outputs = outputs[indomain, :]

    # Make sure streamlines begin within domain with respect to y
    yout = outputs[0, 1] > mg.ly/4 or outputs[0, 1] > mg.ly/4
    if disp is True:
        starty = outputs[0, 1]
    elif yout is True:
        starty = np.random.uniform(-mg.ly/4, mg.ly/4)
    else:
        starty = 0
    outputs[:, 1] = outputs[:, 1] - starty


    return outputs


def ferguson_theta(s, eps_factor, k, h):
    """
    Calculate channel direction angle

    """
    # Storage arrays
    th_store = np.zeros(len(s))

    for idex, si in enumerate(s):
        if idex == 0:
            t1 = 0
            t2 = 0
            eps = 0
        elif idex == 1:
            t1 = th_store[idex-1]
            t2 = 0
            eps = np.random.normal()*eps_factor
        else:
            t1 = th_store[idex-1]
            t2 = th_store[idex-2]
            eps = np.random.normal(1)*eps_factor

        th_store[idex] = thetaAR2(t1, t2, k, h, eps)

    return th_store


def thetaAR2(t1, t2, k, h, eps):
    """
    Implementation of AR2 autoregressive model (Ferguson, 1976, Eq.15)
    http://onlinelibrary.wiley.com/doi/10.1002/esp.3290010403/full

    t1: theta(i-1)
    t2: theta(i-2)
    """
    b1 = 2*np.exp(-k*h)*np.cos(k*np.arcsin(h))
    b2 = -np.exp(-2*k*h)
    return eps + b1*t1 + b2*t2


"""--------------------------------------------------------------------------------------------------------------
Sheet generators and utilities
--------------------------------------------------------------------------------------------------------------"""


def gen_sheet(sh, mg, ae_i, ae_array, count, ani=True):
    """
    Generate gravel sheet with internal heterogeneity
    Args:
        sh:         sheet parameters
        mg:         model grid class
        ae_i:       architectural element lookup details [sequence number, z_bottom, z_top, architectural element, geometry]
        ae_array:   architectural element array

    Returns:

    """
    # Initialize storage arrays
    if ani:
         mat, fac, azim, dip = save_arrays((mg.nx, mg.ny, mg.nz))
    else:
        mat, fac = save_arrays((mg.nx, mg.ny, mg.nz), ani=False)

    # Massive bedding -----------------------------------
    if sh['lens_thickness'] == -1:
        count += 1

        # Generate dip
        if 'r_dip' in sh and np.diff(sh['r_dip']) != 0:
            do, fd, dv, av = dip_sets(mg, sh, ae_i[1])               # Generate facies sets
            fac[ae_array == ae_i[0]] = fd[ae_array == ae_i[0]]
            mat[ae_array == ae_i[0]] = do[ae_array == ae_i[0]] + count
            count += len(do)
            if ani:
                azim[ae_array == ae_i[0]] = 0
                dip[ae_array == ae_i[0]] = dv
        else:
            # No dip
            mat[ae_array == ae_i[0]] = count
            fac[ae_array == ae_i[0]] = random.choice(sh['l_facies'])
            if ani:
                azim[ae_array == ae_i[0]] = 0
                dip[ae_array == ae_i[0]] = 0

    # Create lenses over depths ------------------------------
    else:

        # Assign lens thickness for sequence
        if 'r_geo_ztrend' in sh:
            zfactor = np.interp(np.mean(ae_i[1:3]), [mg.oz, mg.oz + mg.lz], [sh['r_geo_ztrend'][0], sh['r_geo_ztrend'][1]])
            z_lens_thick = sh['lens_thickness'] * zfactor
        else:
            z_lens_thick = sh['lens_thickness']
        z_lens = np.arange(ae_i[1], ae_i[2]*1.1, z_lens_thick)          # Buffer added to top elevation to avoid non-assignment

        # Loop over lenses
        for znow in z_lens:
            count += 1
            z_bottom = mg.idx_z(znow)
            z_top = min(mg.idx_z(znow + sh['lens_thickness']), mg.nz)
            z_range = range(z_bottom, z_top)

            # Generate dip
            if np.array(sh['r_dip']).ptp() > 0:
                do, fd, dv, av = dip_sets(mg, sh, znow)               # Generate facies sets

                # Iterate over all nodes - Brute force approach :(
                it = np.nditer(ae_array, flags=['multi_index'])
                while not it.finished:
                    if it.multi_index[2] in z_range and ae_array[it.multi_index] == ae_i[0]:
                        fac[it.multi_index] = fd[it.multi_index]
                        mat[it.multi_index] = do[it.multi_index] + count

                    it.iternext()

                count += len(np.unique(do))
                if ani:
                    azim[:, :, z_range] = av
                    dip[:, :, z_range] = dv   # Assign facies sets to storage arrays
            else:
                fac[:, :, z_range] = random.choice(sh['l_facies'])
                mat[:, :, z_range] = count
                if ani:
                    azim[:, :, z_range] = 0
                    dip[:, :, z_range] = 0

    if ani:
        props = {'mat': mat, 'azim': azim, 'dip': dip, 'fac': fac}
    else:
        props = {'mat': mat, 'fac': fac}
    return props, count


def dip_sets(mg, aep, znow, channel=[], select=[], azimuth_z=0):
    """
    Generate dip angles and assign to the dip matrix

    Args:
        mg:         Mesh grid object class
        aep:        Architectural element parameters (dict)
        channel:    Tuple of x,y coordinates of channel (omitted for linear flows)
                        - x, y coordinates of channel
                        - vx, vy of channel flow
        select:     Model grid nodes to assign

    Returns:
        dip_out:    Array of assigned dip values
        fac_out:    Array of assigned hydrofacies

    """

    # Vectors of spatial coordinates of grid
    # xgvec, ygvec, zgvec = mg.vec()                              # Grid vectors
    xtemp, ytemp, ztemp = mg.meshup()      # 3-D grid

    # Define series of points for plane equations
    if len(channel) > 1:
        # Interpolate points along the channel trajectory
        x_dip, y_dip = curve_interp(channel[0], channel[1], aep['dipset_d'])
    else:
        if 'r_azimuth' in aep:
            azimuth_z += np.random.uniform(aep['r_azimuth'][0], aep['r_azimuth'][1])
        xst = -aep['dipset_d']*20 + np.random.uniform(0, aep['dipset_d'])        # Starting x-coordinate of plane points
        xend = mg.lx * 1.5 - xst                                                  # Final x-coordinate of plane points

        # Get coordinate differences
        lamb_dip = np.arange(xst, xend, aep['dipset_d'])
        xpvec = lamb_dip * np.cos(np.deg2rad(azimuth_z))
        ypvec = lamb_dip * np.sin(np.deg2rad(azimuth_z))

        # Calculate coordinates of dip points
        x_dip = xst + xpvec
        y_dip = 0 + ypvec

    # Calculate normal vector components in x/y by getting the difference between points
    p_setlamb = (xpvec**2 + ypvec**2) ** 0.5

    # Define normal vector (This might change if the plane is angled (i.e. channel settings)
    dip_z = np.random.uniform(aep['r_dip'][0], aep['r_dip'][1])
    dip_set = np.ones(np.shape(xpvec)) * dip_z
    dip_norm = np.array([xpvec, ypvec, p_setlamb * np.tan(np.deg2rad(90 - dip_set))]) #np.array((1, 0, np.tan(np.deg2rad(90 - aep['r_dip'][1]))))

    set_no = planepoint(dip_norm, x_dip, y_dip, znow, xtemp, ytemp, ztemp, select)
    # Re-index set_no, starting from 1 to work with 'count'
    set_no = reindex(set_no) + 1

    """ Assign hydrofacies """
    # Initialise hydrofacies array
    fac_set = np.zeros((mg.nx, mg.ny, mg.nz), dtype=int)

    if 'll_altfacies' in aep:
        # Alternating hydrofacies
        ae_fac = np.asarray(aep['l_facies'], dtype=int)
        fac_now = random.choice(ae_fac)       # Initialise previous facies
        for idi in np.unique(set_no):
            pf_i = int(np.where(fac_now == ae_fac)[0])                                         # Get previous facies index
            fac_now = random.choice(aep['ll_altfacies'][pf_i])  # Get next alternating facies
            fac_set[set_no == idi] = fac_now                                                    # Set previous facies

        for idx, facies in enumerate(aep['l_facies']):      # Cycle over hydrofacies in element
            fac_set[np.mod(set_no, idx + 1) == 0] = facies
    else:
        # Random assignment of hydrofacies
        for idi in np.unique(set_no):
            fac_set[set_no == idi] = np.random.choice(aep['l_facies'])

    return set_no, fac_set, dip_z, azimuth_z


def curve_interp(xc, yc, spacing):
    """
    Interpolate evenly spaced points along a curve. This code is based on code in an answer posted by 'Unutbu' on
    http://stackoverflow.com/questions/19117660/how-to-generate-equispaced-interpolating-values (retrieved 17/04/2017)

    Args:
        xc:
        yc:
        spacing:

    Returns:
        xn:
        yn:

    """

    t = np.arange(xc[0], len(xc), spacing * 0.1)
    xc = np.interp(t, np.arange(len(xc)), xc)
    yc = np.interp(t, np.arange(len(yc)), yc)
    tol = spacing
    ic, idx = 0, [0]
    while ic < len(xc):
        total_dist = 0
        for j in range(ic+1, len(xc)):
            total_dist += math.sqrt((xc[j] - xc[j-1]) ** 2 + (yc[j] - yc[j-1]) ** 2)
            if total_dist > tol:
                idx.append(j)
                break
        ic = j + 1

    xn = xc[idx]
    yn = yc[idx]
    # fig, ax = plt.subplots()
    # ax.plot(xc, yc, '-')
    # ax.scatter(xn, yn)
    # ax.set_aspect('equal')
    # plt.show()

    return xn, yn


def dip_rotate(azimuth_in, dip_in):
    """
    Rotate dip angle based on azimuth
    Note that inputs and outputs are in degrees

    Args:
        azimuth_in:
        dip_in:
    """
    azimuth_in = azimuth_in * np.pi / 180
    dip_in = dip_in * np.pi / 180
    dip_out = np.arctan((np.sin(azimuth_in) + np.cos(azimuth_in) * np.tan(dip_in)) /
                        (np.cos(azimuth_in) - np.sin(azimuth_in) * np.tan(dip_in))) * 180 / np.pi
    return dip_out



"""--------------------------------------------------------------------------------------------------------------
Assignment of hydraulic properties
--------------------------------------------------------------------------------------------------------------"""

"""--------------------------------------------------------------------------------------------------------------
General functions
--------------------------------------------------------------------------------------------------------------"""


def save_arrays(arr_size, bg=False, mat_count=0, ani=True):
    """
    Generate arrays for material properties storage

    Args:
        arr_size:       Size of array
        bg:             List of background values for each array
        ani:            Boolean if anisotropy is to be generated

    Returns:

    """
    if bg is False:
        bg = np.zeros((3,))

    mat = np.ones(arr_size, dtype=np.int32) * mat_count             # initialize material
    fac = np.ones(arr_size, dtype=np.int16) * int(bg[0])              # initialize hydrofacies

    if ani is True:
        azim = np.ones(arr_size, dtype=np.float32) * bg[1]     # initialize azimuth angle
        dip = np.ones(arr_size, dtype=np.float32) * bg[2]       # initialize dip angle
        return mat, fac, azim, dip
    else:
        return mat, fac


def prob_choose(choices, probs):
    ae_list = []
    for chi in range(0, len(choices)):
        ae_list += [choices[chi]] * int(probs[chi] * 1000)
    choice = random.choice(ae_list)

    return choice


def angle(v1, v2):
    """ Return angle between two vectors in [°] """
    return np.arccos(np.abs(np.dot(v1, v2)) / (np.sqrt(np.sum(v1 ** 2, axis=1)) * np.sqrt(np.sum(v2 ** 2))))


def reindex(inray):
    """ Reindex array from 0 """
    remat = dict(zip(np.unique(inray), np.arange(len(np.unique(inray)))))      # Dict of old & new indices
    vecmat = np.vectorize(remat.get)
    return vecmat(inray) + 1


def channel_checker(param_file, ae_name, no_channels=1, dist=0):
    """
    channel_checker function for quickly assessing the shape of channel inputs

    Returns:

    """
    run, model, sequences, hydraulics, flowtrans, elements, mg = hu.model_setup(param_file)
    ch_par = elements[ae_name]

    plt.figure()
    for i in range(no_channels):
        chs = ferguson_channel(mg, ch_par['h'], ch_par['k'],  ch_par['ds'], ch_par['eps_factor'], dist=dist, disp=True)
        plt.plot(chs[:, 0], chs[:, 1])
        plt.axes().set_aspect('equal', 'datalim')

    plt.show()


def planepoint(dip_norm, x_dip, y_dip, znow, xtemp, ytemp, ztemp, select=[]):
    """ Get closest plane to points

    Args:
        dip_norm:
        x_dip:
        y_dip:
        znow:
        xtemp:
        ytemp:
        ztemp:
        select:         Model grid nodes to consider

    Returns:

    """
    # Get closest plane to points
    n_sets = dip_norm.shape[1]                   # Number of planes
    nx, ny, nz = xtemp.shape                       # Get number of model cells
    set_no = np.zeros((nx, ny, nz), dtype=np.int)  # Initialise set number array
    z_dip = np.ones(x_dip.shape) * znow

    points = np.array((xtemp[select].flatten(), ytemp[select].flatten(), ztemp[select].flatten()))      # Cartesian coordinates of model grid nodes
    plp = np.array((x_dip, y_dip, z_dip)).T                                     # Cartesian coordinates of points on dip planes
    pd = plp[:, None] - points.T                                                # subtract grid nodes from plane points

    select_idx = np.where(select)                                               # Get indices of selected model nodes

    # Loop over set planes
    for iset in range(n_sets-1):
        abc_1 = dip_norm[:, iset]                                                           # Plane normal equation
        pd_1 = abc_1.dot(pd[iset, :, :].squeeze().T) / np.sqrt(sum(abc_1 * abc_1))          # Distance to plane
        pd1_c1 = pd_1 <= 0                                                                  # pd_1 meeting condition 1
        pd1_c1_idx = np.where(pd1_c1)

        if iset == 0:
            set_no[select_idx[0][pd1_c1_idx], select_idx[1][pd1_c1_idx], select_idx[2][pd1_c1_idx]] = iset+1
        elif iset == n_sets:
            pd1_c2_idx = np.where(pd_1 > 0)                     # index of pd_2 meeting condition 1
            set_no[select_idx[0][pd1_c2_idx], select_idx[1][pd1_c2_idx], select_idx[2][pd1_c2_idx]] = iset+1
        else:
            abc_2 = dip_norm[:, iset+1]
            # Points on plane
            pd_2 = abc_2.dot(pd[iset+1, :, :].squeeze().T) / np.sqrt(sum(abc_2 * abc_2))  # Distance to plane
            inset = np.logical_and(pd_1 <= 0, pd_2 > 0)                                   # grid cell between planes
            set_no[select_idx[0][inset], select_idx[1][inset], select_idx[2][inset]] = iset+1

    return set_no


"""--------------------------------------------------------------------------------------------------------------
Testing functions
--------------------------------------------------------------------------------------------------------------"""
if __name__ == '__main__':
    param = '../../../hyvr/examples/made/made.ini'
    main(param)