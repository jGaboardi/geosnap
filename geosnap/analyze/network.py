import geopandas as gpd
import pandas as pd
from libpysal.cg import alpha_shape_auto
from shapely import concave_hull
from shapely.geometry import MultiPoint


def _geom_to_hull(geom, ratio, allow_holes):
    return concave_hull(MultiPoint(geom.tolist()), ratio=ratio, allow_holes=allow_holes)

def _geom_to_alpha(geom):
    return alpha_shape_auto(geom.get_coordinates()[["x", "y"]].values)

def _points_to_poly(df, column, hull="shapely", ratio=0.2, allow_holes=False):
    if hull == "libpysal":
        output = df.groupby(column)["geometry"].apply(_geom_to_alpha)
    elif hull == "shapely":
        output = df.groupby(column)["geometry"].apply(
            lambda x: _geom_to_hull(x, ratio, allow_holes)
        )
    else:
        raise ValueError(
            f"`algorithm must be either 'shapely' or 'libpysal' but {hull} was passed"
        )
    return output


def pdna_to_adj(origins, network, threshold, reindex=True, drop_nonorigins=True):
    """Create an adjacency list of shortest network-based travel between
       origins and destinations in a pandana.Network.

    Parameters
    ----------
    origins : geopandas.GeoDataFrame
        Geodataframe of origin geometries to begin routing. If geometries are
        polygons, they will be collapsed to centroids
    network : pandana.Network
        pandana.Network instance that stores the local travel network
    threshold : int
        maximum travel distance (inclusive)
    reindex : bool, optional
        if True, use geodataframe index to identify observations in the
        adjlist. If False, the node_id from the OSM node nearest each
        observation will be used. by default True
    drop_nonorigins : bool, optional
        If True, drop any destination nodes that are not also origins,
        by default True

    Returns
    -------
    pandas.DataFrame
        adjacency list with columns 'origin', 'destination', and 'cost'
    """
    node_ids = network.get_node_ids(origins.centroid.x, origins.centroid.y).astype(int)

    # map node ids in the network to index in the gdf
    mapper = dict(zip(node_ids, origins.index.values))

    namer = {"source": "origin", "distance": "cost"}

    adj = network.nodes_in_range(node_ids, threshold)
    adj = adj.rename(columns=namer)
    # swap osm ids for gdf index
    if reindex:
        adj = adj.set_index("destination").rename(index=mapper).reset_index()
        adj = adj.set_index("origin").rename(index=mapper).reset_index()
    if drop_nonorigins:
        adj = adj[adj.destination.isin(origins.index.values)]

    return adj


def isochrones_from_id(
    origin, network, threshold, hull="shapely", ratio=0.2, allow_holes=False
):
    """Create travel isochrone(s) from a single origin using a pandana network.

    Parameters
    ----------
    origin : int or list
        A single or list of node id(s) from a `pandana.Network.nodes_df`
        to serve as isochrone origins
    network : pandana.Network
        A pandana network object
    threshold : int or list
        A single or list of threshold distances for which isochrones will be
        computed. These are in the
        same units as edges from the pandana.Network.edge_df
    hull : str, {'libpysal', 'shapely'}
        Which method to generate container polygons (concave hulls) for destination
        points. If 'libpysal', use `libpysal.cg.alpha_shape_auto` to create the
        concave hull, else if 'shapely', use  `shapely.concave_hull`.
        Default is libpysal
    ratio : float
        ratio keyword passed to `shapely.concave_hull`. Only used if
        `algorithm='hull'`. Default is 0.3
    allow_holes : bool
        keyword passed to `shapely.concave_hull` governing  whether holes are
        allowed in the resulting polygon. Only used if `algorithm='hull'`.
        Default is False.

    Returns
    -------
    geopandas.GeoDataFrame
        A geodataframe with a single attribute (distance) and a polygon
        geometry representing a travel time isochrone, with a row for each
        threshold distance
    """
    dfs = []

    # create a geodataframe of nodes from the network
    node_df = gpd.GeoDataFrame(
        network.nodes_df,
        geometry=gpd.points_from_xy(network.nodes_df.x, network.nodes_df.y),
        crs=4326,
    )

    maxdist = max(threshold) if isinstance(threshold, list) else threshold

    matrix = pdna_to_adj(
        origins=node_df[node_df.index == origin],
        network=network,
        threshold=maxdist,
        reindex=False,
        drop_nonorigins=False,
    )

    if not isinstance(threshold, list):
        threshold = [threshold]
    threshold.sort(reverse=True)

    for distance in threshold:
        # select the nodes within each threshold distance and take their alpha shape
        df = matrix[matrix.cost <= distance]
        nodes = node_df[node_df.index.isin(df.destination.tolist())]
        if hull == "libpysal":
            alpha = _geom_to_alpha(nodes.geometry)
        elif hull == "shapely":
            alpha = _geom_to_hull(nodes.geometry, ratio=ratio, allow_holes=allow_holes)
        else:
            raise ValueError(
                f"`algorithm must be either 'alpha' or 'hull' but {hull} was passed"
            )

        alpha = gpd.GeoDataFrame(geometry=pd.Series(alpha), crs=4326)
        alpha["distance"] = distance

        dfs.append(alpha)

    alpha = pd.concat(dfs).reset_index(drop=True)

    return alpha


def isochrones_from_gdf(
    origins,
    threshold,
    network,
    network_crs=None,
    reindex=True,
    hull="shapely",
    ratio=0.2,
    allow_holes=False,
):
    """Create travel isochrones for several origins simultaneously

        Parameters
        ----------
        origins : geopandas.GeoDataFrame
            a geodataframe containing the locations of origin point features
        threshold: float
            maximum travel distance to define the isochrone, measured in the same
            units as edges_df in the pandana.Network object. If the network was
            created with pandana this is usually meters; if it was created with
            urbanaccess this is usually travel time in minutes.
        network : pandana.Network
            pandana Network instance for calculating the shortest path isochrone
            for each origin feature
        network_crs : str, int, pyproj.CRS (optional)
            the coordinate system used to store x and y coordinates in the passed
            pandana network. If None, the network is assumed to be stored in the 
            same CRS as the origins geodataframe
        reindex : bool
            if True, use the dataframe index as the origin and destination IDs
            (rather than the node_ids of the pandana.Network). Default is True
        hull : str, {'libpysal', 'shapely'}
            Which method to generate container polygons (concave hulls) for destination
            points. If 'libpysal', use `libpysal.cg.alpha_shape_auto` to create the
            concave hull, else if 'shapely', use  `shapely.concave_hull`.
            Default is libpysal
        ratio : float
            ratio keyword passed to `shapely.concave_hull`. Only used if
            `hull='shapely'`. Default is 0.3
        allow_holes : bool
            keyword passed to `shapely.concave_hull` governing  whether holes are
            allowed in the resulting polygon. Only used if `hull='shapely'`.
            Default is False.

        Returns
        -------
        GeoPandas.DataFrame
            polygon geometries with the isochrones for each origin point feature

    """
    if network_crs is None:
        network_crs = origins.crs
    node_ids = network.get_node_ids(origins.centroid.x, origins.centroid.y).astype(int)

    # map node ids in the network to index in the gdf
    mapper = dict(zip(node_ids, origins.index.values))

    destinations = gpd.GeoDataFrame(
        network.nodes_df,
        geometry=gpd.points_from_xy(network.nodes_df.x, network.nodes_df.y),
        crs=network_crs,
    )
    matrix = pdna_to_adj(
        origins,
        network=network,
        threshold=threshold,
        reindex=False,
        drop_nonorigins=False,
    )
    alphas = []
    for origin in matrix.origin.unique():
        do = matrix[matrix.origin == origin]
        dest_pts = destinations.loc[do["destination"]]
        if hull == "libpysal":
            alpha = _geom_to_alpha(dest_pts.geometry)
        elif hull == "shapely":
            alpha = _geom_to_hull(
                dest_pts.geometry, ratio=ratio, allow_holes=allow_holes
            )
        else:
            raise ValueError(
                f"`algorithm must be either 'alpha' or 'hull' but {hull} was passed"
            )

        alpha = gpd.GeoDataFrame(geometry=pd.Series(alpha), crs=network_crs)
        alpha["distance"] = threshold
        alpha["origin"] = origin
        alphas.append(alpha)
        df = pd.concat(alphas, ignore_index=True)
        df = df.set_index("origin")
        if reindex:
            df = df.rename(index=mapper)
    return gpd.GeoDataFrame(df, crs=network_crs)


