# This is a work in progress as we try to synthesize ideas from the 
# table based methods and energy ratios back into one thing, 
# some ideas we're incorporating:

# Conversion from polars to pandas
# Constructing tables (but now including tables of ratios)
# Keeping track of frequencies is matching sized tables

import warnings
import numpy as np
import polars as pl

from flasc.energy_ratio.energy_ratio_output import EnergyRatioOutput
from flasc.energy_ratio.energy_ratio_input import EnergyRatioInput
from flasc.energy_ratio.energy_ratio_utilities import add_ws_bin, add_wd, add_wd_bin, add_power_ref, add_power_test, add_reflected_rows


# Internal version, returns a polars dataframe
def _compute_energy_ratio_single(df_,
                         df_names,
                         ref_cols,
                         test_cols,
                         wd_cols,
                         ws_cols,
                         wd_step = 2.0,
                         wd_min = 0.0,
                         wd_max = 360.0,
                         ws_step = 1.0,
                         ws_min = 0.0,
                         ws_max = 50.0,
                         bin_cols_in = ['wd_bin','ws_bin'],
                         wd_bin_overlap_radius = 0.
                         ):

    """
    Compute the energy ratio between two sets of turbines.

    Args:
        df_ (pl.DataFrame): A dataframe containing the data to use in the calculation.
        df_names (list): A list of names to give to the dataframes. 
        ref_cols (list[str]): A list of columns to use as the reference turbines
        test_cols (list[str]): A list of columns to use as the test turbines
        wd_cols (list[str]): A list of columns to derive the wind directions from
        ws_cols (list[str]): A list of columns to derive the wind speeds from
        wd_step (float): The width of the wind direction bins.
        wd_min (float): The minimum wind direction to use.
        wd_max (float): The maximum wind direction to use.
        ws_step (float): The width of the wind speed bins.
        ws_min (float): The minimum wind speed to use.
        ws_max (float): The maximum wind speed to use.
        bin_cols_in (list[str]): A list of column names to use for the wind speed and wind direction bins.
        wd_bin_overlap_radius (float): The distance in degrees one wd bin overlaps into the next, must be 
            less or equal to half the value of wd_step

    Returns:
        pl.DataFrame: A dataframe containing the energy ratio for each wind direction bin
    """

    # Identify the number of dataframes
    num_df = len(df_names)

    # Filter df_ that all the columns are not null
    df_ = df_.filter(pl.all(pl.col(ref_cols + test_cols + ws_cols + wd_cols).is_not_null()))

    # If wd_bin_overlap_radius is not zero, add reflected rows
    if wd_bin_overlap_radius > 0.:

        # Need to obtain the wd column now rather than during binning
        df_ = add_wd(df_, wd_cols)

        # Add reflected rows
        edges = np.arange(wd_min, wd_max + wd_step, wd_step)
        df_ = add_reflected_rows(df_, edges, wd_bin_overlap_radius)

    # Assign the wd/ws bins
    df_ = add_ws_bin(df_, ws_cols, ws_step, ws_min, ws_max)
    df_ = add_wd_bin(df_, wd_cols, wd_step, wd_min, wd_max)

    

    # Assign the reference and test power columns
    df_ = add_power_ref(df_, ref_cols)
    df_ = add_power_test(df_, test_cols)

    bin_cols_without_df_name = [c for c in bin_cols_in if c != 'df_name']
    bin_cols_with_df_name = bin_cols_without_df_name + ['df_name']

    
    
    df_ = (df_
        .filter(pl.all(pl.col(bin_cols_with_df_name).is_not_null())) # Select for all bin cols present
        .groupby(bin_cols_with_df_name, maintain_order=True)
        .agg([pl.mean("pow_ref"), pl.mean("pow_test"),pl.count()]) 
        .with_columns(
            [
                pl.col('count').min().over(bin_cols_without_df_name).alias('count_min')#, # Find the min across df_name
            ]
        )
        .with_columns(
            [
                pl.col('pow_ref').mul(pl.col('count_min')).alias('ref_energy'), # Compute the reference energy
                pl.col('pow_test').mul(pl.col('count_min')).alias('test_energy'), # Compute the test energy
            ]
        )
        .groupby(['wd_bin','df_name'], maintain_order=True)
        .agg([pl.sum("ref_energy"), pl.sum("test_energy"),pl.sum("count")])
        .with_columns(
            energy_ratio = pl.col('test_energy') / pl.col('ref_energy')
        )
        .pivot(values=['energy_ratio','count'], columns='df_name', index='wd_bin',aggregate_function='first')
        .rename({f'energy_ratio_df_name_{n}' : n for n in df_names})
        .rename({f'count_df_name_{n}' : f'count_{n}'  for n in df_names})
        .sort('wd_bin')
    )

    # In the case of two turbines, compute an uplift column
    if num_df == 2:
        df_ = df_.with_columns(
            uplift = 100 * (pl.col(df_names[1]) - pl.col(df_names[0])) / pl.col(df_names[0])
        )

        # Enforce a column order
        df_ = df_.select(['wd_bin'] + df_names + ['uplift'] + [f'count_{n}' for n in df_names])

    else:
        # Enforce a column order
        df_ = df_.select(['wd_bin'] + df_names +  [f'count_{n}' for n in df_names])


    return(df_)

# Bootstrap function wraps the _compute_energy_ratio function
def _compute_energy_ratio_bootstrap(er_in,
                         df_names,
                         ref_cols,
                         test_cols,
                         wd_cols,
                         ws_cols,
                         wd_step = 2.0,
                         wd_min = 0.0,
                         wd_max = 360.0,
                         ws_step = 1.0,
                         ws_min = 0.0,
                         ws_max = 50.0,
                         bin_cols_in = ['wd_bin','ws_bin'],
                         wd_bin_overlap_radius = 0.,
                         N = 1,
                         parallell_interface="serial",  # Options are  'serial 'multiprocessing', 'mpi4py'
                         max_workers=None,
                         ):
    
    """
    Compute the energy ratio between two sets of turbines with bootstrapping

    Args:
        er_in (EnergyRatioInput): An EnergyRatioInput object containing the data to use in the calculation.
        df_names (list): A list of names to give to the dataframes. 
        ref_cols (list[str]): A list of columns to use as the reference turbines
        test_cols (list[str]): A list of columns to use as the test turbines
        wd_cols (list[str]): A list of columns to derive the wind directions from
        ws_cols (list[str]): A list of columns to derive the wind speeds from
        wd_step (float): The width of the wind direction bins.
        wd_min (float): The minimum wind direction to use.
        wd_max (float): The maximum wind direction to use.
        ws_step (float): The width of the wind speed bins.
        ws_min (float): The minimum wind speed to use.
        ws_max (float): The maximum wind speed to use.
        bin_cols_in (list[str]): A list of column names to use for the wind speed and wind direction bins.
        wd_bin_overlap_radius (float): The distance in degrees one wd bin overlaps into the next, must be 
            less or equal to half the value of wd_step
        N (int): The number of bootstrap samples to use.
        parallell_interface (str): The interface to use for parallelization. Options are 'serial', 'multiprocessing', 'mpi4py'
        max_workers (int): The maximum number of workers to use for parallelization. If None, use all available workers.

    Returns:
        pl.DataFrame: A dataframe containing the energy ratio between the two sets of turbines.

    """

    # Otherwise run the function N times and concatenate the results to compute statistics
    if parallell_interface == "serial":
        df_concat = pl.concat([_compute_energy_ratio_single(er_in.resample_energy_table(i),
                            df_names,
                            ref_cols,
                            test_cols,
                            wd_cols,
                            ws_cols,
                            wd_step,
                            wd_min,
                            wd_max,
                            ws_step,
                            ws_min,
                            ws_max,
                            bin_cols_in,
                            wd_bin_overlap_radius,
                            ) for i in range(N)])
    else:
        # The parallel computing interface to use
        if parallell_interface == "mpi4py":
            import mpi4py.futures as mp
            _PoolExecutor = mp.MPIPoolExecutor
        elif parallell_interface == "multiprocessing":
            import multiprocessing as mp
            _PoolExecutor = mp.Pool
            if max_workers is None:
                max_workers = mp.cpu_count()
        # elif interface == "concurrent":
        #     from concurrent.futures import ProcessPoolExecutor
        #     self._PoolExecutor = ProcessPoolExecutor
        else:
            raise UserWarning(
                f"Interface '{parallell_interface}' not recognized. "
                "Please use 'serial', 'multiprocessing' or 'mpi4py'."
            )
        
        # Assemble the agurments
        multiargs = [(er_in.resample_energy_table(i),
                            df_names,
                            ref_cols,
                            test_cols,
                            wd_cols,
                            ws_cols,
                            wd_step,
                            wd_min,
                            wd_max,
                            ws_step,
                            ws_min,
                            ws_max,
                            bin_cols_in,
                            wd_bin_overlap_radius,
                            ) for i in range(N)]
        
        with _PoolExecutor(max_workers) as p:
            # This code is not currently necessary, but leaving in case implement
            # concurrent later, based on parallel_computing_interface.py
            if (parallell_interface == "mpi4py") or (parallell_interface == "multiprocessing"):
                    out = p.starmap(_compute_energy_ratio_single, multiargs)

                    df_concat = pl.concat(out)

    if 'uplift' in df_concat.columns:
        df_names_with_uplift = df_names + ['uplift']
    else:
        df_names_with_uplift = df_names

    return (df_concat
            .groupby(['wd_bin'], maintain_order=True)
            .agg([pl.first(n) for n in df_names_with_uplift] + 
                    [pl.quantile(n, 0.95).alias(n + "_ub") for n in df_names_with_uplift] +
                    [pl.quantile(n, 0.05).alias(n + "_lb") for n in df_names_with_uplift] + 
                    [pl.first(f'count_{n}') for n in df_names]
                )
            .sort('wd_bin')
            )
    

def compute_energy_ratio(er_in: EnergyRatioInput,
                         df_names=None,
                         ref_turbines=None,
                         test_turbines= None,
                         wd_turbines=None,
                         ws_turbines=None,
                         use_predefined_ref = False,
                         use_predefined_wd = False,
                         use_predefined_ws = False,
                         wd_step = 2.0,
                         wd_min = 0.0,
                         wd_max = 360.0,
                         ws_step = 1.0,
                         ws_min = 0.0,
                         ws_max = 50.0,
                         bin_cols_in = ['wd_bin','ws_bin'],
                         wd_bin_overlap_radius = 0.,
                         N = 1,
                                                  parallell_interface="serial",  # Options are  'serial 'multiprocessing', 'mpi4py'
                         max_workers=None,
                         )-> EnergyRatioOutput:
    
    """
    Compute the energy ratio between two sets of turbines with bootstrapping

    Args:
        er_in (EnergyRatioInput): An EnergyRatioInput object containing the data to use in the calculation.
        df_names (list): A list of names to give to the dataframes. 
        ref_turbines (list[int]): A list of turbine numbers to use as the reference.
        test_turbines (list[int]): A list of turbine numbers to use as the test.
        ws_turbines (list[int]): A list of turbine numbers to use for the wind speeds
        wd_turbines (list[int]): A list of turbine numbers to use for the wind directions
        use_predefined_ref (bool): If True, use the pow_ref column of df_ as the reference power.
        use_predefined_ws (bool): If True, use the ws column of df_ as the wind speed.
        use_predefined_wd (bool): If True, use the wd column of df_ as the wind direction.
        wd_step (float): The width of the wind direction bins.
        wd_min (float): The minimum wind direction to use.
        wd_max (float): The maximum wind direction to use.
        ws_step (float): The width of the wind speed bins.
        ws_min (float): The minimum wind speed to use.
        ws_max (float): The maximum wind speed to use.
        bin_cols_in (list[str]): A list of column names to use for the wind speed and wind direction bins.
        wd_bin_overlap_radius (float): The distance in degrees one wd bin overlaps into the next, must be 
            less or equal to half the value of wd_step
        N (int): The number of bootstrap samples to use.

    Returns:
        EnergyRatioOutput: An EnergyRatioOutput object containing the energy ratio between the two sets of turbines.

    """

    # Get the polars dataframe from within the er_in
    df_ = er_in.get_df()

    # Check that the inputs are valid
    # If use_predefined_ref is True, df_ must have a column named 'pow_ref'
    if use_predefined_ref:
        if 'pow_ref' not in df_.columns:
            raise ValueError('df_ must have a column named pow_ref when use_predefined_ref is True')
        # If ref_turbines supplied, warn user that it will be ignored
        if ref_turbines is not None:
            warnings.warn('ref_turbines will be ignored when use_predefined_ref is True')
    else:
        # ref_turbine must be supplied
        if ref_turbines is None:
            raise ValueError('ref_turbines must be supplied when use_predefined_ref is False')
        
    # If use_predefined_ws is True, df_ must have a column named 'ws'
    if use_predefined_ws:
        if 'ws' not in df_.columns:
            raise ValueError('df_ must have a column named ws when use_predefined_ws is True')
        # If ws_turbines supplied, warn user that it will be ignored
        if ws_turbines is not None:
            warnings.warn('ws_turbines will be ignored when use_predefined_ws is True')
    else:
        # ws_turbine must be supplied
        if ws_turbines is None:
            raise ValueError('ws_turbines must be supplied when use_predefined_ws is False')

    # If use_predefined_wd is True, df_ must have a column named 'wd'
    if use_predefined_wd:
        if 'wd' not in df_.columns:
            raise ValueError('df_ must have a column named wd when use_predefined_wd is True')
        # If wd_turbines supplied, warn user that it will be ignored
        if wd_turbines is not None:
            warnings.warn('wd_turbines will be ignored when use_predefined_wd is True')
    else:
        # wd_turbine must be supplied
        if wd_turbines is None:
            raise ValueError('wd_turbines must be supplied when use_predefined_wd is False')
        

    # Confirm that test_turbines is a list of ints or a numpy array of ints
    if not isinstance(test_turbines, list) and not isinstance(test_turbines, np.ndarray):
        raise ValueError('test_turbines must be a list or numpy array of ints')

    # Confirm that test_turbines is not empty  
    if len(test_turbines) == 0:
        raise ValueError('test_turbines cannot be empty')
    
    # Confirm that wd_bin_overlap_radius is less than or equal to wd_step/2
    if wd_bin_overlap_radius > wd_step/2:
        raise ValueError('wd_bin_overlap_radius must be less than or equal to wd_step/2')
    
     # Set up the column names for the reference and test power
    if not use_predefined_ref:
        ref_cols = [f'pow_{i:03d}' for i in ref_turbines]
    else:
        ref_cols = ['pow_ref']

    if not use_predefined_ws:
        ws_cols = [f'ws_{i:03d}' for i in ws_turbines]
    else:
        ws_cols = ['ws']

    if not use_predefined_wd:
        wd_cols = [f'wd_{i:03d}' for i in wd_turbines]
    else:
        wd_cols = ['wd']

    if df_names is None:
        df_names = df_['df_name'].unique().to_list()

    # Convert the numbered arrays to appropriate column names
    test_cols = [f'pow_{i:03d}' for i in test_turbines]

    # If N=1, don't use bootstrapping
    if N == 1:
        # Compute the energy ratio
        df_res = _compute_energy_ratio_single(df_,
                        df_names,
                        ref_cols,
                        test_cols,
                        wd_cols,
                        ws_cols,
                        wd_step,
                        wd_min,
                        wd_max,
                        ws_step,
                        ws_min,
                        ws_max,
                        bin_cols_in,
                        wd_bin_overlap_radius)
    else:
        df_res = _compute_energy_ratio_bootstrap(er_in,
                            df_names,
                            ref_cols,
                            test_cols,
                            wd_cols,
                            ws_cols,
                            wd_step,
                            wd_min,
                            wd_max,
                            ws_step,
                            ws_min,
                            ws_max,
                            bin_cols_in,
                            wd_bin_overlap_radius,
                            N)

    # Return the results as an EnergyRatioOutput object
    return EnergyRatioOutput(df_res, 
                                df_names,
                                er_in,
                                ref_cols, 
                                test_cols, 
                                wd_cols,
                                ws_cols,
                                wd_step,
                                wd_min,
                                wd_max,
                                ws_step,
                                ws_min,
                                ws_max,
                                bin_cols_in,
                                wd_bin_overlap_radius,
                                N)





# # Use method of Eric Simley's slide 2
# def _compute_uplift_in_region_single(df_,
#                          df_names,
#                          ref_cols,
#                          test_cols,
#                          wd_cols,
#                          ws_cols,
#                          wd_step = 2.0,
#                          wd_min = 0.0,
#                          wd_max = 360.0,
#                          ws_step = 1.0,
#                          ws_min = 0.0,
#                          ws_max = 50.0,
#                          bin_cols_in = ['wd_bin','ws_bin']
#                          ):
    
#     """
#     Compute the energy  uplift between two dataframes using method of Eric Simley's slide 2
#     Args:
#         df_ (pl.DataFrame): A dataframe containing the data to use in the calculation.
#         df_names (list): A list of names to give to the dataframes. 
#         ref_cols (list[str]): A list of columns to use as the reference turbines
#         test_cols (list[str]): A list of columns to use as the test turbines
#         wd_cols (list[str]): A list of columns to derive the wind directions from
#         ws_cols (list[str]): A list of columns to derive the wind speeds from
#         wd_step (float): The width of the wind direction bins.
#         wd_min (float): The minimum wind direction to use.
#         wd_max (float): The maximum wind direction to use.
#         ws_step (float): The width of the wind speed bins.
#         ws_min (float): The minimum wind speed to use.
#         ws_max (float): The maximum wind speed to use.
#         bin_cols_in (list[str]): A list of column names to use for the wind speed and wind direction bins.

#     Returns:
#         pl.DataFrame: A dataframe containing the energy uplift
#     """

#     # Filter df_ that all the columns are not null
#     df_ = df_.filter(pl.all(pl.col(ref_cols + test_cols + ws_cols + wd_cols).is_not_null()))

#     # Assign the wd/ws bins
#     df_ = add_ws_bin(df_, ws_cols, ws_step, ws_min, ws_max)
#     df_ = add_wd_bin(df_, wd_cols, wd_step, wd_min, wd_max)

#     # Assign the reference and test power columns
#     df_ = add_power_ref(df_, ref_cols)
#     df_ = add_power_test(df_, test_cols)

#     bin_cols_without_df_name = [c for c in bin_cols_in if c != 'df_name']
#     bin_cols_with_df_name = bin_cols_without_df_name + ['df_name']
    
#     df_ = (df_.with_columns(
#             power_ratio = pl.col('pow_test') / pl.col('pow_ref'))
#         .filter(pl.all(pl.col(bin_cols_with_df_name).is_not_null())) # Select for all bin cols present
#         .groupby(bin_cols_with_df_name, maintain_order=True)
#         .agg([pl.mean("pow_ref"), pl.mean("power_ratio"),pl.count()]) 
#         .with_columns(
#             [
#                 pl.col('count').min().over(bin_cols_without_df_name).alias('count_min'), # Find the min across df_name
#                 pl.col('pow_ref').mul(pl.col('power_ratio')).alias('pow_test'), # Compute the test power
#             ]
#         )

#         .pivot(values=['power_ratio','pow_test','pow_ref','count_min'], columns='df_name', index=['wd_bin','ws_bin'],aggregate_function='first')
#         .drop_nulls()
#         .with_columns(
#             f_norm = pl.col(f'count_min_df_name_{df_names[0]}') / pl.col(f'count_min_df_name_{df_names[0]}').sum()
#         )
#         .with_columns(
#             delta_power_ratio = pl.col(f'power_ratio_df_name_{df_names[1]}') - pl.col(f'power_ratio_df_name_{df_names[0]}'),
#             pow_ref_both_cases = pl.concat_list([f'pow_ref_df_name_{n}' for n in df_names]).list.mean() 
#         )
#         .with_columns(
#             delta_energy = pl.col('delta_power_ratio') * pl.col('f_norm') * pl.col('pow_ref_both_cases'), # pl.col(f'pow_ref_df_name_{df_names[0]}'),
#             base_test_energy = pl.col(f'pow_test_df_name_{df_names[0]}') * pl.col('f_norm')
#         )

#     )

#     return pl.DataFrame({'delta_energy':8760 * df_['delta_energy'].sum(),
#                             'base_test_energy':8760 * df_['base_test_energy'].sum(),
#                             'uplift':100 * df_['delta_energy'].sum() / df_['base_test_energy'].sum()})
                            

# def _compute_uplift_in_region_bootstrap(df_,
#                          df_names,
#                          ref_cols,
#                          test_cols,
#                          wd_cols,
#                          ws_cols,
#                          wd_step = 2.0,
#                          wd_min = 0.0,
#                          wd_max = 360.0,
#                          ws_step = 1.0,
#                          ws_min = 0.0,
#                          ws_max = 50.0,
#                          bin_cols_in = ['wd_bin','ws_bin'],
#                          N = 20,
#                          ):
    
#     """
#     Compute the uplift in a region using bootstrap resampling

#     Args:
#         df_ (pl.DataFrame): A dataframe containing the data to use in the calculation.
#         df_names (list): A list of names to give to the dataframes. 
#         ref_cols (list[str]): A list of columns to use as the reference turbines
#         test_cols (list[str]): A list of columns to use as the test turbines
#         wd_cols (list[str]): A list of columns to derive the wind directions from
#         ws_cols (list[str]): A list of columns to derive the wind speeds from
#         ws_step (float): The width of the wind speed bins.
#         ws_min (float): The minimum wind speed to use.
#         ws_max (float): The maximum wind speed to use.
#         wd_step (float): The width of the wind direction bins.
#         wd_min (float): The minimum wind direction to use.
#         wd_max (float): The maximum wind direction to use.
#         bin_cols_in (list[str]): A list of column names to use for the wind speed and wind direction bins.
#         N (int): The number of bootstrap samples to use.

#     Returns:
#         pl.DataFrame: A dataframe containing the energy uplift
#     """
    
#     df_concat = pl.concat([_compute_uplift_in_region_single(resample_energy_table(df_, i),
#                           df_names,
#                          ref_cols,
#                          test_cols,
#                          wd_cols,
#                          ws_cols,
#                          wd_step,
#                          wd_min,
#                          wd_max,
#                          ws_step,
#                          ws_min,
#                          ws_max,
#                          bin_cols_in,
#                          ) for i in range(N)])
    
#     return pl.DataFrame({
#         'delta_energy_exp':df_concat['delta_energy'][0],
#         'delta_energy_ub':df_concat['delta_energy'].quantile(0.95),
#         'delta_energy_lb':df_concat['delta_energy'].quantile(0.05),
#         'base_test_energy_exp':df_concat['base_test_energy'][0],
#         'base_test_energy_ub':df_concat['base_test_energy'].quantile(0.95),
#         'base_test_energy_lb':df_concat['base_test_energy'].quantile(0.05),
#         'uplift_exp':df_concat['uplift'][0],
#         'uplift_ub':df_concat['uplift'].quantile(0.95),
#         'uplift_lb':df_concat['uplift'].quantile(0.05),
#     })


# def compute_uplift_in_region(df_,
#                          df_names,
#                          ref_turbines=None,
#                          test_turbines= None,
#                          wd_turbines=None,
#                          ws_turbines=None,
#                          use_predefined_ref = False,
#                          use_predefined_wd = False,
#                          use_predefined_ws = False,
#                          wd_step = 2.0,
#                          wd_min = 0.0,
#                          wd_max = 360.0,
#                          ws_step = 1.0,
#                          ws_min = 0.0,
#                          ws_max = 50.0,
#                          bin_cols_in = ['wd_bin','ws_bin'],
#                          N = 1,
#                          ):
    
#     """
#     Compute the energy ratio between two sets of turbines with bootstrapping

#     Args:
#         df_ (pl.DataFrame): A dataframe containing the data to use in the calculation.
#         df_names (list): A list of names to give to the dataframes. 
#         ref_turbines (list[int]): A list of turbine numbers to use as the reference.
#         test_turbines (list[int]): A list of turbine numbers to use as the test.
#         ws_turbines (list[int]): A list of turbine numbers to use for the wind speeds
#         wd_turbines (list[int]): A list of turbine numbers to use for the wind directions
#         use_predefined_ref (bool): If True, use the pow_ref column of df_ as the reference power.
#         use_predefined_ws (bool): If True, use the ws column of df_ as the wind speed.
#         use_predefined_wd (bool): If True, use the wd column of df_ as the wind direction.
#         wd_step (float): The width of the wind direction bins.
#         wd_min (float): The minimum wind direction to use.
#         wd_max (float): The maximum wind direction to use.
#         ws_step (float): The width of the wind speed bins.
#         ws_min (float): The minimum wind speed to use.
#         ws_max (float): The maximum wind speed to use.
#         bin_cols_in (list[str]): A list of column names to use for the wind speed and wind direction bins.
#         N (int): The number of bootstrap samples to use.

#     Returns:
#         pl.DataFrame: A dataframe containing the energy ratio between the two sets of turbines.

#     """

#     # Check if inputs are valid
#     # If use_predefined_ref is True, df_ must have a column named 'pow_ref'
#     if use_predefined_ref:
#         if 'pow_ref' not in df_.columns:
#             raise ValueError('df_ must have a column named pow_ref when use_predefined_ref is True')
#         # If ref_turbines supplied, warn user that it will be ignored
#         if ref_turbines is not None:
#             warnings.warn('ref_turbines will be ignored when use_predefined_ref is True')
#     else:
#         # ref_turbine must be supplied
#         if ref_turbines is None:
#             raise ValueError('ref_turbines must be supplied when use_predefined_ref is False')
        
#     # If use_predefined_ws is True, df_ must have a column named 'ws'
#     if use_predefined_ws:
#         if 'ws' not in df_.columns:
#             raise ValueError('df_ must have a column named ws when use_predefined_ws is True')
#         # If ws_turbines supplied, warn user that it will be ignored
#         if ws_turbines is not None:
#             warnings.warn('ws_turbines will be ignored when use_predefined_ws is True')
#     else:
#         # ws_turbine must be supplied
#         if ws_turbines is None:
#             raise ValueError('ws_turbines must be supplied when use_predefined_ws is False')

#     # If use_predefined_wd is True, df_ must have a column named 'wd'
#     if use_predefined_wd:
#         if 'wd' not in df_.columns:
#             raise ValueError('df_ must have a column named wd when use_predefined_wd is True')
#         # If wd_turbines supplied, warn user that it will be ignored
#         if wd_turbines is not None:
#             warnings.warn('wd_turbines will be ignored when use_predefined_wd is True')
#     else:
#         # wd_turbine must be supplied
#         if wd_turbines is None:
#             raise ValueError('wd_turbines must be supplied when use_predefined_wd is False')
        
#     # Confirm that test_turbines is a list of ints or a numpy array of ints
#     if not isinstance(test_turbines, list) and not isinstance(test_turbines, np.ndarray):
#         raise ValueError('test_turbines must be a list or numpy array of ints')

#     # Confirm that test_turbines is not empty  
#     if len(test_turbines) == 0:
#         raise ValueError('test_turbines cannot be empty')

#     num_df = len(df_names)

#     # Confirm num_df == 2
#     if num_df != 2:
#         raise ValueError('Number of dataframes must be 2')

#     # Set up the column names for the reference and test power
#     if not use_predefined_ref:
#         ref_cols = [f'pow_{i:03d}' for i in ref_turbines]
#     else:
#         ref_cols = ['pow_ref']

#     if not use_predefined_ws:
#         ws_cols = [f'ws_{i:03d}' for i in ws_turbines]
#     else:
#         ws_cols = ['ws']

#     if not use_predefined_wd:
#         wd_cols = [f'wd_{i:03d}' for i in wd_turbines]
#     else:
#         wd_cols = ['wd']

#     # Convert the numbered arrays to appropriate column names
#     test_cols = [f'pow_{i:03d}' for i in test_turbines]

#     # If N=1, don't use bootstrapping
#     if N == 1:
#         # Compute the energy ratio
#         df_res = _compute_uplift_in_region_single(df_,
#                         df_names,
#                         ref_cols,
#                         test_cols,
#                         wd_cols,
#                         ws_cols,
#                         wd_step,
#                         wd_min,
#                         wd_max,
#                         ws_step,
#                         ws_min,
#                         ws_max,
#                         bin_cols_in)
#     else:
#         df_res = _compute_uplift_in_region_bootstrap(df_,
#                             df_names,
#                             ref_cols,
#                             test_cols,
#                             wd_cols,
#                             ws_cols,
#                             wd_step,
#                             wd_min,
#                             wd_max,
#                             ws_step,
#                             ws_min,
#                             ws_max,
#                             bin_cols_in,
#                             N)

#     # Return the results as an EnergyRatioResult object
#     return EnergyRatioResult(df_res, 
#                                 df_names,
#                                 df_,
#                                 ref_cols, 
#                                 test_cols, 
#                                 wd_cols,
#                                 ws_cols,
#                                 wd_step,
#                                 wd_min,
#                                 wd_max,
#                                 ws_step,
#                                 ws_min,
#                                 ws_max,
#                                 bin_cols_in,
#                                 N)


