"""
eedl.py - FINAL V5 with Pixel Dimensions
For orbital cameras: Accepts pixel dimensions and GSD, computes geographic area automatically.
Uses 'dimensions' parameter for EPSG:4326 to get exact pixel count.
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
import requests
from tqdm.contrib.concurrent import process_map

from utils.config_utils import USER_CONFIG_PATH, load_config
from utils.earth_utils import get_MGRS_grid

ee.Initialize(project='argus-cubesat')

def get_region_filter_from_bounds(bounds, get_rect=True):
    region_left, region_bottom, region_right, region_top = bounds
    # FIX: Correct order is [left, bottom, right, top]
    rect_from_bounds = ee.Geometry.Rectangle([region_left, region_bottom, region_right, region_top])

    if args.sensor == 's2':
        out_rect = rect_from_bounds.buffer(500000)
    else:
        out_rect = rect_from_bounds
    region_filter_from_bounds = ee.Filter.bounds(out_rect)
    if get_rect:
        return region_filter_from_bounds, rect_from_bounds
    return region_filter_from_bounds

def get_date_filter(i_date, f_date):
    ee_date_filter = ee.Filter.date(i_date, f_date)
    return ee_date_filter

def get_collection(sensor, ee_region_filter, ee_date_filter, ee_bands = None, cloud_cover_min = 0.0, cloud_cover_max = 30.0, date_sort=True):
    if sensor == 'l8':
        collection_string = 'LANDSAT/LC08/C02/T1_TOA'
        cloud_string = 'CLOUD_COVER'
    elif sensor == 'l9':
        collection_string = 'LANDSAT/LC09/C02/T1_TOA'
        cloud_string = 'CLOUD_COVER'
    elif sensor == 's2':
        collection_string = 'COPERNICUS/S2_HARMONIZED'
        cloud_string = 'CLOUDY_PIXEL_PERCENTAGE'

    if ee_bands is None:
        ee_bands = ['B4', 'B3', 'B2']

    ee_collection = ee.ImageCollection(collection_string)
    ee_collection = ee_collection.filter(ee_date_filter)
    if not args.custom_mosaics:
        ee_collection = ee_collection.filter(ee_region_filter)
    print("cloud_cover_max = ", cloud_cover_max)
    ee_collection = ee_collection.filter(ee_date_filter)
    ee_collection = ee_collection.filter(ee.Filter.lt(cloud_string, cloud_cover_max))
    ee_collection = ee_collection.filter(ee.Filter.gte(cloud_string, cloud_cover_min))
    ee_collection = ee_collection.select(ee_bands)
    if date_sort:
        ee_collection = ee_collection.sort('DATE_ACQUIRED')
    return ee_collection

def get_points_in_region(ee_region, num_points, pts_scale, pts_seed):
    water_land_data = ee.ImageCollection('MODIS/061/MCD12Q1')
    land = water_land_data.select('LW').first()
    mask = land.eq(2)
    selected_points = land.updateMask(mask).stratifiedSample(region=ee_region, scale = pts_scale,
                                                    classBand = 'LW', numPoints = num_points,
                                                    geometries=True,seed = pts_seed)
    return selected_points.aggregate_array('.geo').getInfo()

def make_rectangle(ee_point, h_pt_buffer, v_pt_buffer = None):
    """
    Creates a rectangle geometry around a given point.
    Handles EPSG:4326 (lat/lon) with geodesic=True and meter-to-degree conversion.
    Handles UTM projections with geodesic=False and meter-based buffers.
    """
    if v_pt_buffer is None:
        v_pt_buffer = h_pt_buffer
    coords = ee_point['coordinates']

    if args.crs:
        projection = args.crs
    elif args.grid_key is None:
        projection = "EPSG:4326"
    elif args.grid_key[-1] <= 'M':
        projection = "EPSG:327" + args.grid_key[:-1]
    else:
        projection = "EPSG:326" + args.grid_key[:-1]

    if projection == "EPSG:4326":
        lat = coords[1]
        lon = coords[0]

        h_buffer_deg = h_pt_buffer / (111000 * np.cos(np.radians(lat)))
        v_buffer_deg = v_pt_buffer / 111000

        west = lon - h_buffer_deg
        south = lat - v_buffer_deg
        east = lon + h_buffer_deg
        north = lat + v_buffer_deg

        # FIX: Remove .bounds() and use default evenOdd=True
        pt_rect = ee.Geometry.Rectangle([west, south, east, north],
                                       projection, True)
    else:
        transformer = pyproj.Transformer.from_crs("EPSG:4326", projection, always_xy=True)
        transformed_pt = tuple(transformer.transform(coords[0], coords[1]))

        pt_tl_x = transformed_pt[0] - h_pt_buffer
        pt_tl_y = transformed_pt[1] + v_pt_buffer
        pt_br_x = transformed_pt[0] + h_pt_buffer
        pt_br_y = transformed_pt[1] - v_pt_buffer

        # FIX: Remove .bounds() and use default evenOdd=True
        pt_rect = ee.Geometry.Rectangle([pt_tl_x, pt_br_y, pt_br_x, pt_tl_y],
                                       projection, False)

    return pt_rect

def get_url(index):
    image = ee.Image(im_list.get(index))
    if args.crs:
        crs = args.crs
    else:
        crs = 'EPSG:4326'

    if args.sensor in ('l8','l9'):
        image = image.multiply(255/0.3).toByte()
        image = image.clip(image.geometry())
    url = image.getDownloadURL({
        'scale':scale,
        'format':out_format,
        'bands':bands,
        'crs':crs})
    return url

@retry(tries=10, delay=1, backoff=2)
def get_and_download_url(index):
    url = get_url(index)
    print('Retrieved URL',index,':',url)
    if not os.path.exists(out_path):
        os.makedirs(out_path)
        print(out_path, 'folder created')
    if out_format == 'GEOTiff':
        ext = '.tif'
    else:
        ext = '.png'
    out_name = args.sensor + '_' + region_name + '_' + str(index).zfill(5) + ext
    r = requests.get(url, stream=True)
    if r.status_code !=200:
        r.raise_for_status()
    with open(os.path.join(out_path,out_name),'wb') as out_file:
        shutil.copyfileobj(r.raw, out_file)
    print('Download',out_name, 'done')

def argument_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('-b', '--bounds', nargs='+', type=int, default=[-84, 24, -78, 32])
    parser.add_argument('-g', '--grid_key', type=str)
    parser.add_argument('-i', '--idate',type=str, default='2022')
    parser.add_argument('-f', '--fdate',type=str, default='2023')
    parser.add_argument('-s', '--scale', type = float, default = 150.0)
    parser.add_argument('-m', '--maxims', type = int, default = 100)
    parser.add_argument('-se', '--sensor', choices=['l8', 'l9', 's2'], type=str, default = 'l8')
    parser.add_argument('-o', '--outpath', type=str, default = 'images')
    parser.add_argument('-r', '--region', type=str, default=None)
    parser.add_argument('-e', '--format', type=str,default = 'GEOTiff', choices=['GEOTiff'])
    parser.add_argument('-sd', '--seed', type=int,default = None)
    parser.add_argument('-c', '--crs', type=str, default = None)
    parser.add_argument('-cc', '--cloud_cover_max',type=float, default = 10.0)
    parser.add_argument('-ccgt', '--cloud_cover_min', type=float, default = 0.0)
    parser.add_argument('-ba','--bands',type=str,nargs='+',default =['B4','B3','B2'])
    parser.add_argument('-cm', '--custom_mosaics', type=bool, default = False)
    
    # NEW: Pixel dimensions instead of buffers
    parser.add_argument('-wp', '--width_pixels', type=int, default=None,
                        help='Image width in pixels (overrides horizontal_buffer)')
    parser.add_argument('-hp', '--height_pixels', type=int, default=None,
                        help='Image height in pixels (overrides vertical_buffer)')
    
    # Legacy buffer arguments (deprecated but kept for backward compatibility)
    parser.add_argument('-vb', '--vertical_buffer', type=float, default=None,
                        help='DEPRECATED: Use --height_pixels instead')
    parser.add_argument('-hb', '--horizontal_buffer', type=float, default=None,
                        help='DEPRECATED: Use --width_pixels instead')
    
    parser.add_argument('-gd', '--gdrive', type=bool, default = False)
    parser.add_argument('-np', '--nprocs', type=int, default = None)
    parser.add_argument('-rm', '--region_mosaic', type=bool, default = False)
    parser.add_argument('-rc', '--region_composite', type=bool, default = False)
    
    parsed_args = parser.parse_args()
    
    if parsed_args.region is None:
        parsed_args.region = parsed_args.grid_key
    if parsed_args.nprocs is None:
        parsed_args.nprocs = cpu_count()
    
    # Handle pixel dimensions vs buffers
    if parsed_args.width_pixels is not None and parsed_args.height_pixels is not None:
        # Calculate buffers from pixel dimensions and GSD
        # Buffer is half the total dimension
        parsed_args.horizontal_buffer = (parsed_args.width_pixels * parsed_args.scale) / 2
        parsed_args.vertical_buffer = (parsed_args.height_pixels * parsed_args.scale) / 2
        print(f"Using pixel dimensions: {parsed_args.width_pixels} × {parsed_args.height_pixels}")
        print(f"Calculated buffers: H={parsed_args.horizontal_buffer:.0f}m, V={parsed_args.vertical_buffer:.0f}m")
        print(f"Geographic coverage: {(parsed_args.width_pixels * parsed_args.scale)/1000:.2f}km × {(parsed_args.height_pixels * parsed_args.scale)/1000:.2f}km")
    elif parsed_args.horizontal_buffer is None or parsed_args.vertical_buffer is None:
        # Set default buffers if neither pixels nor buffers specified
        parsed_args.horizontal_buffer = 425088
        parsed_args.vertical_buffer = 318816
        print("Using default buffer dimensions")
    else:
        print(f"Using buffer dimensions: H={parsed_args.horizontal_buffer:.0f}m, V={parsed_args.vertical_buffer:.0f}m")
    
    return parsed_args

args = argument_parser()
scale = args.scale
max_ims = args.maxims
out_path = args.outpath
out_format = args.format
region_name = args.region
bands = args.bands
horizontal_buffer = args.horizontal_buffer
vertical_buffer = args.vertical_buffer

# Calculate pixel dimensions for display
width_pixels = int((horizontal_buffer * 2) / scale)
height_pixels = int((vertical_buffer * 2) / scale)

if args.grid_key:
    grid = get_MGRS_grid()
    left, bottom, right, top = grid[args.grid_key]
    print("left, bottom, right, top = " , left, bottom, right, top)
    args.bounds = [float(left), float(bottom), float(right), float(top)]

if args.region is None:
    args.region = args.grid_key

if args.seed:
    seed = args.seed
else:
    seed = np.random.randint(100000)

region_filter, region_rect = get_region_filter_from_bounds(args.bounds, get_rect=True)
date_filter = get_date_filter(args.idate, args.fdate)
collection = get_collection(args.sensor, region_filter, date_filter, 
                            ee_bands=bands, cloud_cover_min = args.cloud_cover_min,
                            cloud_cover_max=args.cloud_cover_max, date_sort=True)

if not args.custom_mosaics:
    if args.region_composite:
        task_list = []
        region_rect = region_rect.buffer(10000).bounds()
        print("args.idate = ", args.idate, " and args.fdate = ", args.fdate)
        collection = ee.ImageCollection('LANDSAT/LC08/C02/T1') \
            .filterDate(args.idate, args.fdate) \
            .filterBounds(region_rect)
        composite = ee.Algorithms.Landsat.simpleComposite(collection, 50, 1, 40, True)
        composite = composite.select('B4', 'B3', 'B2')
        composite = composite.divide(0.3).multiply(255).clamp(0, 255).uint8()
        out_name = args.sensor + '_' + region_name + '_composite_' + args.idate + '-' + args.fdate
        if not args.crs:
            crs = 'EPSG:4326'
        else:
            crs = args.crs
        task_config = {
            'scale': scale,
            'fileFormat': out_format,
            'region': region_rect,
            'driveFolder': out_path,
            'crs': crs
        }
        task = ee.batch.Export.image(composite, out_name, task_config)
        task_list.append(task)
            
    elif args.region_mosaic:
        im_list = []
        task_list = []
        if args.seed:
            seed = args.seed
            np.random.seed = seed
        else:
            seed = np.random.randint(100000)
        for i in range(max_ims):          
            collection_with_random_column = collection.randomColumn('random',np.random.randint(100000))
            collection_with_random_column = collection_with_random_column.sort('random')
            collection_with_random_column = ee.ImageCollection(collection_with_random_column)
            MULTIPLIER = 255/0.3
            if args.sensor == 's2':
                MULTIPLIER = MULTIPLIER*0.0001    
            im = collection_with_random_column.mosaic().multiply(MULTIPLIER).toByte()
            out_name = args.sensor + '_' + region_name + '_' + str(i).zfill(5)
            task_config = {
                'scale': scale,
                'fileFormat': out_format,
                'region': region_rect,
                'driveFolder': out_path,
                'crs': 'EPSG:4326'
            }
            task = ee.batch.Export.image(im, out_name, task_config)
            task_list.append(task)
            im_list.append(im)
        im_list = ee.List(im_list)

    elif args.sensor in ('l8', 'l9'):
        print("we are running this part of the script")
        collection = collection.filterBounds(region_rect)
        collection_size = collection.size().getInfo()
        if collection_size < max_ims:
            max_ims = collection_size
        im_list = collection.toList(max_ims)
        if args.gdrive:
            task_list = []
            for i in range(max_ims):
                im = ee.Image(im_list.get(i))
                if args.crs:
                    crs = args.crs
                else:
                    crs = im.select(0).projection().crs().getInfo()
                im = im.multiply(255/0.3).toByte()
                im = im.clip(im.geometry())
                out_name = args.sensor + '_' + region_name + '_' + str(i).zfill(5)
                task_config = {
                    'scale': scale,
                    'fileFormat': out_format,
                    'crs': 'EPSG:4326',
                    'driveFolder': out_path
                }
                task = ee.batch.Export.image.toDrive(im, out_name, **task_config)
                task_list.append(task)

    elif args.sensor == 's2':
        if args.gdrive:
            task_list = []
            if args.seed:
                seed = args.seed
                np.random.seed = seed
            else:
                seed = np.random.randint(100000)
            points = get_points_in_region(region_rect, max_ims, scale, np.random.randint(100000))
            if args.crs:
                proj = args.crs
            elif args.grid_key is None:
                proj = "EPSG:4326"
            elif args.grid_key[-1] <= 'M':
                proj = "EPSG:327" + args.grid_key[:-1]
            else:
                proj = "EPSG:326" + args.grid_key[:-1]
            for i,point in enumerate(points):
                clip_rect = make_rectangle(point, horizontal_buffer, vertical_buffer)
                collection_with_random_column = collection.filterBounds(clip_rect)
                collection_with_random_column = collection_with_random_column.randomColumn('random',np.random.randint(100000))
                collection_with_random_column = collection_with_random_column.sort('random')
                collection_with_random_column = ee.ImageCollection(collection_with_random_column)
                MULTIPLIER = 255/0.3
                if args.sensor == 's2':
                    MULTIPLIER = MULTIPLIER*0.0001    
                im = collection_with_random_column.mosaic().multiply(MULTIPLIER).toByte()
                rect_im = im.clip(clip_rect)
                out_name = args.sensor + '_' + region_name + '_' + str(i).zfill(5)
                
                if proj == "EPSG:4326":
                    task_config = {
                        'dimensions': f'{width_pixels}x{height_pixels}',
                        'fileFormat': out_format,
                        'region': clip_rect,
                        'driveFolder': out_path,
                        'crs': proj
                    }
                else:
                    task_config = {
                        'scale': scale,
                        'fileFormat': out_format,
                        'region': clip_rect,
                        'driveFolder': out_path,
                        'crs': proj
                    }
                
                task = ee.batch.Export.image(rect_im, out_name, task_config)
                task_list.append(task)
        else:
            im_list = []
            if args.seed:
                seed = args.seed
                np.random.seed = seed
            else:
                seed = np.random.randint(100000)
            points = get_points_in_region(region_rect, max_ims, scale, np.random.randint(100000))
            for point in points:
                clip_rect = make_rectangle(point, 185000/2)
                collection_with_random_column = collection.filterBounds(clip_rect)
                collection_with_random_column = collection_with_random_column.randomColumn('random',np.random.randint(100000))
                collection_with_random_column = collection_with_random_column.sort('random')
                collection_with_random_column = ee.ImageCollection(collection_with_random_column)
                im = collection_with_random_column.mosaic().multiply(0.0001).divide(0.3).multiply(255).toByte()
                rect_im = im.clip(clip_rect)
                im_list.append(rect_im)
            im_list = ee.List(im_list)
else:
    im_list = []
    task_list = []
    points = get_points_in_region(region_rect, max_ims, scale, np.random.randint(100000))
    if args.crs:
        proj = args.crs
    elif args.grid_key is None:
        proj = "EPSG:4326"
    elif args.grid_key[-1] <= 'M':
        proj = "EPSG:327" + args.grid_key[:-1]
    else:
        proj = "EPSG:326" + args.grid_key[:-1]
    
    for i,point in enumerate(points):
        clip_rect = make_rectangle(point, horizontal_buffer, vertical_buffer)
        collection_with_random_column = collection.filterBounds(clip_rect)
        collection_with_random_column = collection_with_random_column.randomColumn('random',np.random.randint(100000))
        collection_with_random_column = collection_with_random_column.sort('random')
        collection_with_random_column = ee.ImageCollection(collection_with_random_column)
        MULTIPLIER = 255/0.3
        if args.sensor == 's2':
            MULTIPLIER = MULTIPLIER*0.0001    
        im = collection_with_random_column.mosaic().multiply(MULTIPLIER).toByte()
        rect_im = im.clip(clip_rect)
        out_name = args.sensor + '_' + region_name + '_' + str(i).zfill(5)
        
        if proj == "EPSG:4326":
            print(f"Using dimensions: {width_pixels}x{height_pixels} for EPSG:4326")
            task_config = {
                'dimensions': f'{width_pixels}x{height_pixels}',
                'fileFormat': out_format,
                'region': clip_rect,
                'driveFolder': out_path,
                'crs': proj
            }
        else:
            task_config = {
                'scale': scale,
                'fileFormat': out_format,
                'region': clip_rect,
                'driveFolder': out_path,
                'crs': proj
            }
        
        task = ee.batch.Export.image(rect_im, out_name, task_config)
        task_list.append(task)
    im_list = ee.List(im_list)

if __name__ == '__main__':
    if not args.custom_mosaics:
        if args.gdrive:
            print('Downloading images to Google Drive.')
            print('View status of tasks at: https://code.earthengine.google.com/tasks')
            for task in task_list:
                task.start()
            print(len(task_list), 'tasks started')
        else:
            indexes = range(max_ims)
            print('Downloading images.')
            process_map(get_and_download_url, indexes, max_workers=args.nprocs, chunksize=1)
    else:
        print('Downloading image(s).')      
        for task in task_list:
            task.start()
        print(len(task_list), 'task(s) started')
