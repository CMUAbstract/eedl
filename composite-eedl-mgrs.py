"""
composite-eedl-mgrs.py
Earth Engine Downloader with MGRS Support
A script to download satellite composite images from the Google Earth Engine API.
Modified to accept pixel dimensions and GSD, computing geographic area automatically.
Author: Sun A Cho (Modified)
"""

import argparse
import os
import shutil
from multiprocessing import cpu_count

import ee
import requests
from retry import retry
import numpy as np
import pyproj
from tqdm.contrib.concurrent import process_map

from utils.config_utils import USER_CONFIG_PATH, load_config
from utils.earth_utils import get_MGRS_grid

ee.Initialize(project='argus-cubesat')

def calculate_bounds_from_center(lon, lat, width_m, height_m):
    """
    Calculate bounding box from center point and dimensions in meters.
    
    Parameters:
    lon (float): Center longitude
    lat (float): Center latitude
    width_m (float): Width in meters (east-west)
    height_m (float): Height in meters (north-south)
    
    Returns:
    tuple: (left, bottom, right, top) in degrees
    """
    if lat >= 0:
        zone = int((lon + 180) / 6) + 1
        epsg_code = f"EPSG:326{str(zone).zfill(2)}"
    else:
        zone = int((lon + 180) / 6) + 1
        epsg_code = f"EPSG:327{str(zone).zfill(2)}"
    
    transformer_to_utm = pyproj.Transformer.from_crs("EPSG:4326", epsg_code, always_xy=True)
    transformer_to_wgs84 = pyproj.Transformer.from_crs(epsg_code, "EPSG:4326", always_xy=True)
    
    center_x, center_y = transformer_to_utm.transform(lon, lat)
    
    left_x = center_x - width_m / 2
    right_x = center_x + width_m / 2
    bottom_y = center_y - height_m / 2
    top_y = center_y + height_m / 2
    
    left, bottom_lat = transformer_to_wgs84.transform(left_x, bottom_y)
    right, top_lat = transformer_to_wgs84.transform(right_x, top_y)
    
    return left, bottom_lat, right, top_lat

def generate_grid_points(bounds, num_points=30):
    """
    Generate evenly distributed points within an MGRS grid region.
    
    Parameters:
    bounds (list): [left, bottom, right, top] in degrees
    num_points (int): Number of points to generate (default: 30)
    
    Returns:
    list: List of (lon, lat) tuples
    """
    left, bottom, right, top = bounds
    
    rows = int(np.sqrt(num_points))
    cols = int(np.ceil(num_points / rows))
    
    lons = np.linspace(left, right, cols + 2)[1:-1]
    lats = np.linspace(bottom, top, rows + 2)[1:-1]
    
    points = []
    for lat in lats:
        for lon in lons:
            points.append((lon, lat))
            if len(points) >= num_points:
                break
        if len(points) >= num_points:
            break
    
    return points[:num_points]

def get_region_filter_from_bounds(bounds, get_rect=True):
    """
    Creates a filter for a given geographical rectangle defined by longitude and latitude bounds.

    Parameters:
    bounds (list): A list of four elements [left, bottom, right, top] defining the geographical rectangle.
    get_rect (bool): A flag to determine whether to return the rectangle geometry.

    Returns:
    ee.Filter: A filter that selects images intersecting with the defined rectangle.
    ee.Geometry.Rectangle (optional): The rectangle geometry, returned if get_rect is True.
    """
    region_left, region_bottom, region_right, region_top = bounds
    rect_from_bounds = ee.Geometry.Rectangle([region_left, region_bottom, region_right, region_top])
    out_rect = rect_from_bounds.buffer(10000)
    region_filter_from_bounds = ee.Filter.bounds(out_rect)
    if get_rect:
        return region_filter_from_bounds, rect_from_bounds
    return region_filter_from_bounds

def get_date_filter(i_date, f_date):
    """
    Creates a date filter for selecting images within a specified date range.

    Parameters:
    i_date (str): Initial date of the date range in a format recognizable by the Earth Engine API.
    f_date (str): Final date of the date range in a format recognizable by the Earth Engine API.

    Returns:
    ee.Filter: A date filter for the specified date range.
    """
    ee_date_filter = ee.Filter.date(i_date, f_date)
    return ee_date_filter

def argument_parser():
    """
    Parses command line arguments.
    """
    parser = argparse.ArgumentParser(description="Download composite satellite images from Earth Engine")
    
    location_group = parser.add_mutually_exclusive_group(required=True)
    location_group.add_argument('-lon', '--longitude', type=float,
                        help='Center longitude in degrees')
    location_group.add_argument('-g', '--grid_key', type=str,
                        help='MGRS grid region (e.g., 15V, 11R)')
    
    parser.add_argument('-lat', '--latitude', type=float,
                        help='Center latitude in degrees (required if using --longitude)')
    parser.add_argument('-i', '--idate', type=str, required=True,
                        help='Start date (YYYY-MM-DD)')
    parser.add_argument('-f', '--fdate', type=str, required=True,
                        help='End date (YYYY-MM-DD)')
    
    # Image dimensions in pixels
    parser.add_argument('-wp', '--width_pixels', type=int, default=4608,
                        help='Image width in pixels (default: 4608)')
    parser.add_argument('-hp', '--height_pixels', type=int, default=2592,
                        help='Image height in pixels (default: 2592)')
    
    parser.add_argument('-np', '--num_points', type=int, default=10,
                        help='Number of composite images to generate per MGRS region (default: 10)')
    parser.add_argument('-se', '--sensor', choices=['l8', 'l9'], type=str, default='l8',
                        help='Sensor to use (l8 or l9)')
    parser.add_argument('-s', '--scale', type=float, default=135.0,
                        help='Ground Sample Distance in meters (default: 135)')
    parser.add_argument('-o', '--outpath', type=str, default='composite_images',
                        help='Output directory path')
    parser.add_argument('-r', '--region', type=str, default=None,
                        help='Region name for output file')
    parser.add_argument('-e', '--format', type=str, default='GeoTIFF', 
                        choices=['GeoTIFF'],
                        help='Output format')
    parser.add_argument('-c', '--crs', type=str, default='EPSG:4326',
                        help='Coordinate reference system')
    parser.add_argument('-p', '--percentile', type=int, default=50,
                        help='Percentile for composite (default: 50)')
    parser.add_argument('-csr', '--cloud_score_range', type=int, default=10,
                        help='Cloud score range (default: 10)')
    parser.add_argument('-md', '--max_depth', type=int, default=40,
                        help='Maximum depth for composite (default: 40)')
    parser.add_argument('-ba', '--bands', type=str, nargs='+', default=['B4', 'B3', 'B2'],
                        help='Bands to download (default: B4 B3 B2 for RGB)')
    
    parsed_args = parser.parse_args()
    
    if parsed_args.longitude is not None and parsed_args.latitude is None:
        parser.error("--latitude is required when using --longitude")
    
    if parsed_args.region is None:
        if parsed_args.grid_key:
            parsed_args.region = parsed_args.grid_key
        else:
            parsed_args.region = f"lat{parsed_args.latitude:.2f}_lon{parsed_args.longitude:.2f}"
    
    return parsed_args

def create_composite(lon, lat, point_index=None):
    """
    Create and export a composite image for a specific location.
    
    Parameters:
    lon (float): Center longitude
    lat (float): Center latitude
    point_index (int): Index of the point (for naming), None for single point mode
    
    Returns:
    ee.batch.Task: Export task
    """
    # Calculate geographic dimensions from pixel dimensions and GSD
    width_m = width_pixels * scale
    height_m = height_pixels * scale
    width_km = width_m / 1000
    height_km = height_m / 1000
    
    left, bottom, right, top = calculate_bounds_from_center(lon, lat, width_m, height_m)
    
    print(f"Center: ({lon:.4f}, {lat:.4f})")
    print(f"Pixel dimensions: {width_pixels} × {height_pixels} pixels")
    print(f"GSD: {scale}m")
    print(f"Geographic dimensions: {width_km:.2f}km × {height_km:.2f}km")
    print(f"Bounds: left={left:.4f}, bottom={bottom:.4f}, right={right:.4f}, top={top:.4f}")
    
    bounds = [left, bottom, right, top]
    region_filter, region_rect = get_region_filter_from_bounds(bounds, get_rect=True)
    region_rect_buffered = region_rect.buffer(10000).bounds()
    date_filter = get_date_filter(args.idate, args.fdate)
    
    print(f"Date range: {args.idate} to {args.fdate}")
    
    if args.sensor == 'l8':
        collection_string = 'LANDSAT/LC08/C02/T1'
    elif args.sensor == 'l9':
        collection_string = 'LANDSAT/LC09/C02/T1'
    
    collection = ee.ImageCollection(collection_string) \
        .filterDate(args.idate, args.fdate) \
        .filterBounds(region_rect_buffered)
    
    collection_size = collection.size().getInfo()
    print(f"Found {collection_size} images in collection")
    
    if collection_size == 0:
        print("WARNING: No images found for the specified parameters!")
        return None
    
    print(f"Creating composite with percentile={args.percentile}, cloudScoreRange={args.cloud_score_range}")
    composite = ee.Algorithms.Landsat.simpleComposite(
        collection,
        args.percentile,
        args.cloud_score_range,
        args.max_depth,
        True
    )
    
    composite = composite.select(bands)
    composite = composite.divide(0.3).multiply(255).clamp(0, 255).uint8()
    
    if point_index is not None:
        out_name = f"{args.sensor}_{region_name}_pt{str(point_index).zfill(3)}_composite_{args.idate}_to_{args.fdate}"
    else:
        out_name = f"{args.sensor}_{region_name}_composite_{args.idate}_to_{args.fdate}"
    
    out_name = out_name.replace(':', '-').replace(' ', '_')
    
    # Use exact pixel dimensions for export
    task_config = {
        'dimensions': f'{width_pixels}x{height_pixels}',
        'fileFormat': out_format,
        'region': region_rect,
        'driveFolder': out_path,
        'crs': args.crs
    }
    
    print(f"Exporting to Google Drive folder: {out_path}")
    print(f"Output name: {out_name}")
    
    task = ee.batch.Export.image.toDrive(composite, out_name, **task_config)
    return task

if __name__ == '__main__':
    args = argument_parser()
    
    # Calculate dimensions from pixels and GSD
    width_pixels = args.width_pixels
    height_pixels = args.height_pixels
    scale = args.scale
    
    out_path = args.outpath
    out_format = args.format
    region_name = args.region
    bands = args.bands
    
    try:
        import torch
        torch.cuda.empty_cache()
    except:
        pass
    
    print("="*60)
    print("Earth Engine Composite Image Downloader with MGRS Support")
    print("="*60)
    print(f"Image dimensions: {width_pixels} × {height_pixels} pixels")
    print(f"GSD: {scale}m")
    print(f"Geographic coverage: {(width_pixels * scale)/1000:.2f}km × {(height_pixels * scale)/1000:.2f}km")
    print("="*60)
    
    tasks = []
    
    if args.grid_key:
        print(f"\nProcessing MGRS region: {args.grid_key}")
        print(f"Generating {args.num_points} composite images")
        
        grid = get_MGRS_grid()
        if args.grid_key not in grid:
            print(f"ERROR: Grid key '{args.grid_key}' not found in MGRS database")
            exit(1)
        
        left, bottom, right, top = grid[args.grid_key]
        bounds = [float(left), float(bottom), float(right), float(top)]
        
        print(f"MGRS bounds: left={left}, bottom={bottom}, right={right}, top={top}")
        
        points = generate_grid_points(bounds, args.num_points)
        
        print(f"\nGenerated {len(points)} sampling points")
        print("Starting composite generation...")
        print("")
        
        for i, (lon, lat) in enumerate(points):
            print(f"\n[{i+1}/{len(points)}] Processing point at ({lon:.4f}, {lat:.4f})")
            print("-" * 60)
            
            task = create_composite(lon, lat, point_index=i)
            
            if task is not None:
                tasks.append(task)
                print(f"Task {i+1} prepared successfully")
            else:
                print(f"WARNING: Failed to create task for point {i+1}")
            
            print("")
    
    else:
        print("\nProcessing single location")
        task = create_composite(args.longitude, args.latitude)
        if task is not None:
            tasks.append(task)
    
    if tasks:
        print("\n" + "="*60)
        print(f"Starting {len(tasks)} export task(s)...")
        print("="*60)
        
        for i, task in enumerate(tasks):
            task.start()
            print(f"Task {i+1}/{len(tasks)} started")
        
        print(f"\nAll {len(tasks)} tasks submitted successfully!")
        print(f"\nView status at: https://code.earthengine.google.com/tasks")
        print(f"Once complete, check your Google Drive folder: {out_path}")
    else:
        print("\nNo tasks were created. Please check your parameters.")
