package com.protomaps.basemap.layers;

import com.onthegomap.planetiler.FeatureCollector;
import com.onthegomap.planetiler.FeatureMerge;
import com.onthegomap.planetiler.ForwardingProfile;
import com.onthegomap.planetiler.VectorTile;
import com.onthegomap.planetiler.geo.GeometryException;
import com.onthegomap.planetiler.reader.SourceFeature;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

@SuppressWarnings("java:S1192")
public class Choropleth implements ForwardingProfile.LayerPostProcessor {

  public static final String LAYER_NAME = "choropleth";

  private final Map<String, Integer> countryColors = new HashMap<>();
  private final Map<String, Integer> stateColors = new HashMap<>();

  public Choropleth() {
    loadColorMap();
  }

  private void loadColorMap() {
    try (InputStream is = Choropleth.class.getResourceAsStream("/choropleth_colors.csv")) {
      if (is == null) {
        return;
      }
      try (BufferedReader reader = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8))) {
        String line;
        while ((line = reader.readLine()) != null) {
          String[] parts = line.trim().split(",");
          if (parts.length == 3) {
            int c = Integer.parseInt(parts[2]);
            if ("country".equals(parts[0])) {
              countryColors.put(parts[1], c);
            } else if ("region".equals(parts[0])) {
              stateColors.put(parts[1], c);
            }
          }
        }
      }
    } catch (IOException | NumberFormatException ignored) {
      // Fall back to Natural Earth mapcolor attributes if resource unavailable
    }
  }

  @Override
  public String name() {
    return LAYER_NAME;
  }

  private static String getNeString(SourceFeature sf, String key) {
    Object value = sf.getTag(key);
    if (value == null) {
      value = sf.getTag(key.toUpperCase(Locale.ROOT));
    }
    return value == null ? null : value.toString();
  }

  private static int parseColorInt(String val, int fallback) {
    if (val == null || val.isBlank()) {
      return fallback;
    }
    try {
      int n = (int) Math.round(Double.parseDouble(val.trim()));
      return (n >= 1 && n <= 13) ? ((n - 1) % 9) + 1 : fallback;
    } catch (NumberFormatException e) {
      return fallback;
    }
  }

  private static final java.util.Set<String> ADMIN1_COUNTRIES = java.util.Set.of(
    "USA", "CAN", "MEX", "BRA", "ARG", "AUS", "CHN", "IND", "RUS", "IDN", "ZAF"
  );

  public void processNe(SourceFeature sf, FeatureCollector features) {
    if (!sf.canBePolygon()) {
      return;
    }
    String sourceLayer = sf.getSourceLayer();
    if ("ne_50m_admin_0_countries".equals(sourceLayer) || "ne_10m_admin_0_countries".equals(sourceLayer)) {
      int minZoom = sourceLayer.contains("_50m_") ? 0 : 4;
      int maxZoom = sourceLayer.contains("_50m_") ? 3 : 7;
      String adm0 = getNeString(sf, "adm0_a3");
      int fallbackColor = parseColorInt(getNeString(sf, "mapcolor9"), 1);
      int colorId = (adm0 != null && countryColors.containsKey(adm0))
        ? countryColors.get(adm0)
        : fallbackColor;

      features.polygon(LAYER_NAME)
        .setAttr("kind", "country")
        .setAttr("color_id", colorId)
        .setAttr("adm0_a3", adm0)
        .setZoomRange(minZoom, maxZoom)
        .setMinPixelSize(0.5)
        .setPixelTolerance(Earth.PIXEL_TOLERANCE)
        .setBufferPixels(4);
    } else if ("ne_50m_admin_1_states_provinces".equals(sourceLayer) ||
      "ne_10m_admin_1_states_provinces".equals(sourceLayer)) {
      String adm0 = getNeString(sf, "adm0_a3");
      if (adm0 == null || !ADMIN1_COUNTRIES.contains(adm0)) {
        return;
      }
      int minZoom = sourceLayer.contains("_50m_") ? 2 : 5;
      int maxZoom = sourceLayer.contains("_50m_") ? 4 : 7;
      String adm1 = getNeString(sf, "adm1_code");
      int fallbackColor = parseColorInt(getNeString(sf, "mapcolor9"), 1);
      int colorId = (adm1 != null && stateColors.containsKey(adm1))
        ? stateColors.get(adm1)
        : fallbackColor;

      features.polygon(LAYER_NAME)
        .setAttr("kind", "region")
        .setAttr("color_id", colorId)
        .setAttr("adm0_a3", adm0)
        .setZoomRange(minZoom, maxZoom)
        .setMinPixelSize(1.0)
        .setPixelTolerance(Earth.PIXEL_TOLERANCE)
        .setBufferPixels(4);
    }
  }

  @Override
  public List<VectorTile.Feature> postProcess(int zoom, List<VectorTile.Feature> items) throws GeometryException {
    return FeatureMerge.mergeNearbyPolygons(items, 1.0, 1.0, 0.5, Earth.BUFFER);
  }
}
