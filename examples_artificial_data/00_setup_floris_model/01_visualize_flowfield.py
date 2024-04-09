# Copyright 2022 NREL & Shell
import matplotlib.pyplot as plt
import floris.layout_visualization as layoutviz
from floris.flow_visualization import visualize_cut_plane

from flasc.utilities.utilities_examples import load_floris_artificial as load_floris

if __name__ == "__main__":
    # User settings
    wind_speed = 10.0  # Ambient wind speed for the plotted scenario
    wind_direction = 347.0  # Ambient wind direction for the plotted scenario
    turbulence_intensity = 0.06  # Ambient turbulence intensity for the plotted scenario

    plot_height = 90.0  # Height at which we visualize the horizontal flow slice
    x_resolution = (
        500  # Resolution: first grid dimension to calculate wind speed at over the domain (x-)
    )
    y_resolution = (
        500  # Resolution: second grid dimension to calculate wind speed at over the domain (y-)
    )

    # Load FLORIS
    fm, _ = load_floris()
    fm.set(
        wind_directions=[wind_direction],
        wind_speeds=[wind_speed],
        turbulence_intensities=[turbulence_intensity],
    )
    ax = layoutviz.plot_turbine_points(fm)
    ax.grid()
    ax.set_xlabel("x coordinate [m]")
    ax.set_ylabel("y coordinate [m]")
    ax.set_title("Turbine layout")

    # Generate baseline flowfield
    print("Calculating flowfield...")
    fm.run()
    farm_power = fm.get_farm_power().flatten()
    horizontal_plane = fm.calculate_horizontal_plane(
        x_resolution=x_resolution, y_resolution=y_resolution, height=plot_height
    )

    fig, ax = plt.subplots(figsize=(9, 6))
    im = visualize_cut_plane(horizontal_plane, ax=ax, title=None, color_bar=True)
    ax.set_xlabel("x coordinate (m)")
    ax.set_ylabel("y coordinate (m)")
    fig.suptitle(
        "Inflow: {:.1f} m/s, {:.1f} deg, {:.1f} % turbulence.".format(
            wind_speed, wind_direction, turbulence_intensity * 100.0
        )
    )

    plt.tight_layout()
    plt.show()
