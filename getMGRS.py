"""
getMGRS.py
Generates a grid that transforms MGRS (Military Grid Reference System) coordinates to corresponding longitude and latitude ranges.
May return specific longitude and latitude ranges for a given MGRS coordinate if a key is provided.
Author: Kyle McCleary
"""
import argparse

import numpy as np

LONSTEP = 6
LATSTEP = 8

def getMGRS():
    """
    Generate a grid of MGRS (Military Grid Reference System) coordinates.
    Returns:
        dict: A dictionary mapping MGRS coordinates to corresponding longitude and latitude ranges.
    """
    lons = np.arange(-180,180,LONSTEP)
    lats = np.arange(-80,80,LATSTEP)
    lon_labels = np.arange(1,61)
    lat_labels = ['C','D','E','F','G','H','J','K','L','M',
                  'N','P','Q','R','S','T','U','V','W','X']        
    mgrs_grid = {}
    for i, lat_label in enumerate(lat_labels):
        for j, lon_label in enumerate(lon_labels):
            mgrs_grid[str(lon_label).zfill(2) + lat_label] = (lons[j], lats[i],
                                                               lons[j] + LONSTEP, lats[i] + LATSTEP)

    for i in lon_labels:
        idx = str(i).zfill(2)+'X'
        mgrs_grid[idx] = (lons[i-1],72,lons[i-1]+LONSTEP,84)
    mgrs_grid['31V'] = (0,56,3,64)
    mgrs_grid['32V'] = (3,56,12,64)
    mgrs_grid['31X'] = (0,72,9,84)
    mgrs_grid['33X'] = (9,72,21,84)
    mgrs_grid['35X'] = (21,72,33,84)
    mgrs_grid['37X'] = (33,72,42,84)
    del mgrs_grid['32X']
    del mgrs_grid['34X']
    del mgrs_grid['36X']
    return mgrs_grid

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-k','--key',default=None,type=str)
    args = parser.parse_args()
    grid = getMGRS()
    if args.key:
        print(grid[args.key])
    else:
        print(grid)





