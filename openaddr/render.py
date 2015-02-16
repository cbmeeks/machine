from __future__ import division

from glob import glob
from argparse import ArgumentParser
from itertools import combinations
from os.path import join, dirname, basename
import json

from .compat import cairo
from osgeo import ogr, osr

from . import paths

def make_context(width=960, resolution=1):
    ''' Get Cairo surface, context, and drawing scale.
    
        World extent: (-19918964.35, -8269767.91) - (19918964.18, 14041770.96)
    '''
    left, top = -18000000, 14050000
    right, bottom = 19500000, -7500000
    aspect = (right - left) / (top - bottom)

    hsize = int(resolution * width)
    vsize = int(hsize / aspect)

    hscale = hsize / (right - left)
    vscale = (hsize / aspect) / (bottom - top)

    hoffset = -left
    voffset = -top

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, hsize, vsize)
    context = cairo.Context(surface)
    context.scale(hscale, vscale)
    context.translate(hoffset, voffset)
    
    return surface, context, hscale

def load_geoids(directory, good_sources):
    ''' Load a set of U.S. Census GEOIDs that should be rendered.
    '''
    good_geoids, bad_geoids = set(), set()

    for path in glob(join(directory, 'us-*.json')):
        with open(path) as file:
            data = json.load(file)
    
        if 'geoid' in data.get('coverage', {}).get('US Census', {}):
            if basename(path) in good_sources:
                good_geoids.add(data['coverage']['US Census']['geoid'])
            else:
                bad_geoids.add(data['coverage']['US Census']['geoid'])
    
    return good_geoids, bad_geoids

def load_iso3166s(directory, good_sources):
    ''' Load a set of ISO 3166 codes that should be rendered.
    '''
    good_iso3166s, bad_iso3166s = set(), set()

    for path in glob(join(directory, '*.json')):
        with open(path) as file:
            data = json.load(file)
    
        if 'code' in data.get('coverage', {}).get('ISO 3166', {}):
            if basename(path) in good_sources:
                good_iso3166s.add(data['coverage']['ISO 3166']['code'])
            else:
                bad_iso3166s.add(data['coverage']['ISO 3166']['code'])
    
        elif 'alpha2' in data.get('coverage', {}).get('ISO 3166', {}):
            if basename(path) in good_sources:
                good_iso3166s.add(data['coverage']['ISO 3166']['alpha2'])
            else:
                bad_iso3166s.add(data['coverage']['ISO 3166']['alpha2'])
    
    return good_iso3166s, bad_iso3166s

def load_geometries(directory, good_sources):
    ''' Load a set of GeoJSON geometries should be rendered.
    '''
    good_geometries, bad_geometries = list(), list()

    sref_geo = osr.SpatialReference(); sref_geo.ImportFromEPSG(4326)
    sref_map = osr.SpatialReference(); sref_map.ImportFromEPSG(54029)
    project = osr.CoordinateTransformation(sref_geo, sref_map)

    for path in glob(join(directory, '*.json')):
        with open(path) as file:
            data = json.load(file)
    
        if 'geometry' in data.get('coverage', {}):
            geojson = json.dumps(data['coverage']['geometry'])
            geometry = ogr.CreateGeometryFromJson(geojson)
            
            if not geometry:
                continue

            geometry.Transform(project)

            if basename(path) in good_sources:
                good_geometries.append(geometry)
            else:
                bad_geometries.append(geometry)
    
    return good_geometries, bad_geometries

def stroke_features(ctx, features):
    '''
    '''
    return stroke_geometries(ctx, [f.GetGeometryRef() for f in features])
    
def stroke_geometries(ctx, geometries):
    '''
    '''
    for geometry in geometries:
        if geometry.GetGeometryType() in (ogr.wkbMultiPolygon, ogr.wkbMultiLineString):
            parts = geometry
        elif geometry.GetGeometryType() in (ogr.wkbPolygon, ogr.wkbLineString):
            parts = [geometry]
        else:
            continue

        for part in parts:
            if part.GetGeometryType() is ogr.wkbPolygon:
                rings = part
            else:
                rings = [part]

            for ring in rings:
                points = ring.GetPoints()
                if geometry.GetGeometryType() in (ogr.wkbPolygon, ogr.wkbMultiPolygon):
                    draw_line(ctx, points[-1], points)
                else:
                    draw_line(ctx, points[0], points[1:])
                ctx.stroke()

def fill_features(ctx, features):
    '''
    '''
    return fill_geometries(ctx, [f.GetGeometryRef() for f in features])
    
def fill_geometries(ctx, geometries):
    '''
    '''
    for geometry in geometries:
        if geometry.GetGeometryType() == ogr.wkbMultiPolygon:
            parts = geometry
        elif geometry.GetGeometryType() == ogr.wkbPolygon:
            parts = [geometry]
        else:
            raise NotImplementedError()

        for part in parts:
            for ring in part:
                points = ring.GetPoints()
                draw_line(ctx, points[-1], points)
            ctx.fill()

def draw_line(ctx, start, points):
    '''
    '''
    ctx.move_to(*start)

    for point in points:
        ctx.line_to(*point)

parser = ArgumentParser(description='Draw a map of continental U.S. address coverage.')

parser.set_defaults(resolution=1, width=960)

parser.add_argument('--2x', dest='resolution', action='store_const', const=2,
                    help='Draw at double resolution.')

parser.add_argument('--1x', dest='resolution', action='store_const', const=1,
                    help='Draw at normal resolution.')

parser.add_argument('--width', dest='width', type=int,
                    help='Width in pixels.')

parser.add_argument('filename', help='Output PNG filename.')

def main():
    args = parser.parse_args()
    return render(paths.sources, args.width, args.resolution, args.filename)

def render(sources, width, resolution, filename):
    ''' Resolution: 1 for 100%, 2 for 200%, etc.
    '''
    # Prepare output surface
    surface, context, scale = make_context(width, resolution)
    
    # Load state
    import requests, urlparse
    got = requests.get('http://data.openaddresses.io/state.json')
    got = requests.get(urlparse.urljoin(got.url, got.json()))
    columns, rows = got.json()[0], got.json()[1:]
    state = [dict(zip(columns, row)) for row in rows]
    good_sources = set([s['source'] for s in state if (s['cache'] and s['processed'])])

    # Load data
    good_geoids, bad_geoids = load_geoids(sources, good_sources)
    good_iso3166s, bad_iso3166s = load_iso3166s(sources, good_sources)
    good_geometries, bad_geometries = load_geometries(sources, good_sources)

    geodata = join(dirname(__file__), 'geodata')
    coastline_ds = ogr.Open(join(geodata, 'ne_50m_coastline-54029.shp'))
    lakes_ds = ogr.Open(join(geodata, 'ne_50m_lakes-54029.shp'))
    countries_ds = ogr.Open(join(geodata, 'ne_50m_admin_0_countries-54029.shp'))
    countries_borders_ds = ogr.Open(join(geodata, 'ne_50m_admin_0_boundary_lines_land-54029.shp'))
    admin1s_ds = ogr.Open(join(geodata, 'ne_10m_admin_1_states_provinces-54029.shp'))
    us_state_ds = ogr.Open(join(geodata, 'cb_2013_us_state_20m-54029.shp'))
    us_county_ds = ogr.Open(join(geodata, 'cb_2013_us_county_20m-54029.shp'))

    coastline_features = list(coastline_ds.GetLayer(0))
    lakes_features = [f for f in lakes_ds.GetLayer(0) if f.GetField('scalerank') == 0]
    countries_features = list(countries_ds.GetLayer(0))
    countries_borders_features = list(countries_borders_ds.GetLayer(0))
    admin1s_features = list(admin1s_ds.GetLayer(0))
    us_state_features = list(us_state_ds.GetLayer(0))
    us_county_features = list(us_county_ds.GetLayer(0))
    good_data_states = [f for f in us_state_features if f.GetFieldAsString('GEOID') in good_geoids]
    good_data_counties = [f for f in us_county_features if f.GetFieldAsString('GEOID') in good_geoids]
    bad_data_states = [f for f in us_state_features if f.GetFieldAsString('GEOID') in bad_geoids]
    bad_data_counties = [f for f in us_county_features if f.GetFieldAsString('GEOID') in bad_geoids]
    good_data_countries = [f for f in countries_features if f.GetFieldAsString('iso_a2') in good_iso3166s]
    good_data_admin1s = [f for f in admin1s_features if f.GetFieldAsString('iso_3166_2') in good_iso3166s]
    bad_data_countries = [f for f in countries_features if f.GetFieldAsString('iso_a2') in bad_iso3166s]
    bad_data_admin1s = [f for f in admin1s_features if f.GetFieldAsString('iso_3166_2') in bad_iso3166s]
    
    # Draw each border between neighboring states exactly once.
    state_borders = [s1.GetGeometryRef().Intersection(s2.GetGeometryRef())
                     for (s1, s2) in combinations(us_state_features, 2)
                     if s1.GetGeometryRef().Intersects(s2.GetGeometryRef())]
    
    # Fill countries background
    context.set_source_rgb(0xdd/0xff, 0xdd/0xff, 0xdd/0xff)
    fill_features(context, countries_features)

    # Fill populated countries
    context.set_source_rgb(244/0xff, 109/0xff, 67/0xff)
    fill_features(context, bad_data_countries)
    context.set_source_rgb(0x74/0xff, 0xA5/0xff, 0x78/0xff)
    fill_features(context, good_data_countries)

    # Fill Admin-1 (ISO-3166-2) subdivisions
    context.set_source_rgb(244/0xff, 109/0xff, 67/0xff)
    fill_features(context, bad_data_admin1s)
    context.set_source_rgb(0x74/0xff, 0xA5/0xff, 0x78/0xff)
    fill_features(context, good_data_admin1s)

    # Fill populated U.S. states
    context.set_source_rgb(244/0xff, 109/0xff, 67/0xff)
    fill_features(context, bad_data_states)
    context.set_source_rgb(0x74/0xff, 0xA5/0xff, 0x78/0xff)
    fill_features(context, good_data_states)

    # Fill populated U.S. counties
    context.set_source_rgb(215/0xff, 48/0xff, 39/0xff)
    fill_features(context, bad_data_counties)
    context.set_source_rgb(0x1C/0xff, 0x89/0xff, 0x3F/0xff)
    fill_features(context, good_data_counties)

    # Fill other given geometries
    context.set_source_rgb(215/0xff, 48/0xff, 39/0xff)
    fill_geometries(context, bad_geometries)
    context.set_source_rgb(0x1C/0xff, 0x89/0xff, 0x3F/0xff)
    fill_geometries(context, good_geometries)

    # Outline countries and boundaries, fill lakes
    context.set_source_rgb(0, 0, 0)
    context.set_line_width(.25 * resolution / scale)
    stroke_geometries(context, state_borders)
    stroke_features(context, countries_borders_features)

    context.set_source_rgb(0xff/0xff, 0xff/0xff, 0xff/0xff)
    fill_features(context, lakes_features)

    context.set_source_rgb(0, 0, 0)
    context.set_line_width(.5 * resolution / scale)
    stroke_features(context, coastline_features)

    # Output
    surface.write_to_png(filename)

if __name__ == '__main__':
    exit(main())



# Test suite. This code could be in a separate file

import unittest, tempfile, os, tempfile, subprocess

class TestRender (unittest.TestCase):

    def test_render(self):
        sources = join(dirname(__file__), '..', 'tests', 'sources')
        handle, filename = tempfile.mkstemp(prefix='render-', suffix='.png')
        os.close(handle)
        
        try:
            render(sources, 512, 1, filename)
            info = str(subprocess.check_output(('file', filename)))

            self.assertTrue('PNG image data' in info)
            self.assertTrue('512 x 294' in info)
            self.assertTrue('8-bit/color RGBA' in info)
        finally:
            os.remove(filename)
