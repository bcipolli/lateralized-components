# *- encoding: utf-8 -*-
# Author: Ami Tsuchida
# License: BSD
"""
How symmetric are the whole-brain ICA components? How are they similar to
half-brain ICA components?

For both WB and hal-brain components, find the sparsity, measured as L1 norm
and also as voxel count above a threshold, to compare whether they differ.
Sharper contrast (increase in vc-sparsity) in half-brain ICA indicate masking
of lateralized organization in WB.

To analyze symmetry of WB components, Calculate;
1) HPAI (Hemisphere Participation Asymmetry Index)
2) SSS (spatial symmetry score: similarity score between R and L, using correlation)
for each WB ICA component image to show the relationship between the two.

Then for each component, find the best-matching half-brain R&L components,
compare the SSS between them to see how much it  (increases relative to the
whole-brain SSS. Also compare terms associated with whole-brain and matching
half-brain components.

Do that with a hard loop on the # of components, then
plotting the mean SSS change.
"""

import os
import os.path as op

import matplotlib.pyplot as plt
import numpy as np
from scipy import stats
import pandas as pd
import seaborn as sns
from textwrap import wrap

from match import do_match_analysis, get_dataset, load_or_generate_components
from nilearn.image import iter_img
from nilearn.masking import apply_mask
from nilearn_ext.masking import flip_img_lr, get_hemi_gm_mask
from nilearn_ext.plotting import save_and_close, rescale
from nilearn_ext.utils import get_match_idx_pair
from nilearn_ext.decomposition import compare_RL
from sklearn.externals.joblib import Memory


SPARSITY_SIGNS = ['pos', 'neg', 'abs']


def get_sparsity_threshold(images, global_percentile=99.9):
    """
    Given the list of images, get global (across images) sparsity threshold
    using the specified percentile values.

    The global_percentile for each image in each component are obtained,
    and the minimum value is returned.
    """
    global_thr = []
    for image in images:
        g_thr = []
        for component_img in iter_img(image):
            dat = component_img.get_data()
            nonzero_dat = dat[np.nonzero(dat)]
            g = stats.scoreatpercentile(np.abs(nonzero_dat), global_percentile)
            g_thr.append(g)
        global_thr.append(min(g_thr))
    thr = min(global_thr)

    return thr


def get_hemi_sparsity(img, hemi, thr=0.000005):
    """
    Calculate sparsity of the image for the given hemisphere.
    Sparsity is calculated using 1) l1norm ("l1") value of the image, and
    2) voxel count ("vc") for # of voxels above a threshold.

    The vc method is calculated separately for pos, neg side of the image
    and for absolute values, to detect any anti-correlated netowrks.

    It assumes the values of the img is normalized.

    Returns a dict containing arrays for l1, vc-pos, vc-neg, vc-abs, each
    1-vector array with the length (n_component) of the img.
    The dict also contains n_voxels for the given hemi.
    """
    # Transform img to vector for the specified hemisphere
    gm_mask = get_hemi_gm_mask(hemi=hemi)
    masked = apply_mask(img, gm_mask)
    sparsity_dict = {}
    sparsity_dict["l1"] = np.linalg.norm(masked, axis=1, ord=1)
    sparsity_dict["vc-pos"] = (masked > thr).sum(axis=1)
    sparsity_dict["vc-neg"] = (masked < -thr).sum(axis=1)
    sparsity_dict["vc-abs"] = (np.abs(masked) > thr).sum(axis=1)

    return sparsity_dict


def calculate_acni(img, hemi, percentile=95.0):
    """
    For each component image in the give ICA image, calculate Anti-Correlated
    Network Index (ACNI), which is simply a proportion of negative activation
    out of all the voxels whose magnitude is above a given percentile value.

    i.e. the component with value close to 0.5 has strong ACN, with positive and
    negative side of the activation equally balanced, while a value
    close to 0 indicates the component has very little ACN.

    Returns an array of length equal to the n_component of the given image.
    """
    n_components = img.shape[3]

    # Get threshold values for each image based on the given percentile val.
    gm_mask = get_hemi_gm_mask(hemi=hemi)
    masked = apply_mask(img, gm_mask)
    thr = stats.scoreatpercentile(np.abs(masked), percentile, axis=1)
    reshaped_thr = thr.reshape((n_components, 1))

    neg_voxels = np.sum(masked < -reshaped_thr, axis=1)
    abs_voxels = np.sum(np.abs(masked) > reshaped_thr, axis=1)

    acni = np.divide(neg_voxels, abs_voxels.astype(float))

    return acni


def calculate_hpai(wb_img, percentile=95.0):
    """
    Compute HPAI for each component image of the given WB ICA image.

    It is calculated by first taking the voxels whose magnitude is above
    a given percentile (default 95.0), and calculating (R-L)/(R+L) for
    the number of voxels. The L grey matter mask is applied to both sides to
    keep the total numnber of voxels in the hemispheres eaual.

    HPAI is calculated separately for positive, negative, and absolute values,
    and returned as a dictionary with SPARSITY_SIGNS as keys.
    """
    n_components = wb_img.shape[3]

    hpai_d = {}

    # Get threshold values for each image based on the given percentile val.
    gm_mask = get_hemi_gm_mask(hemi="wb")
    wb_masked = apply_mask(wb_img, gm_mask)
    thr = stats.scoreatpercentile(np.abs(wb_masked), percentile, axis=1)
    reshaped_thr = thr.reshape((n_components, 1))

    # Count the number of voxels above the threshold in each hemisphere.
    # Use only lh_masker to ensure the same size
    hemi_mask = get_hemi_gm_mask(hemi="L")
    masked_r = apply_mask(flip_img_lr(wb_img), hemi_mask)
    masked_l = apply_mask(wb_img, hemi_mask)
    for sign in SPARSITY_SIGNS:
        if sign == "pos":
            voxel_r = np.sum(masked_r > reshaped_thr, axis=1)
            voxel_l = np.sum(masked_l > reshaped_thr, axis=1)
        elif sign == "neg":
            voxel_r = np.sum(masked_r < -reshaped_thr, axis=1)
            voxel_l = np.sum(masked_l < -reshaped_thr, axis=1)
        elif sign == "abs":
            voxel_r = np.sum(np.abs(masked_r) > reshaped_thr, axis=1)
            voxel_l = np.sum(np.abs(masked_l) > reshaped_thr, axis=1)

        hpai_d[sign] = np.divide((voxel_r - voxel_l), (voxel_r + voxel_l).astype(float))

    return hpai_d


def load_or_generate_summary(images, term_scores, n_components, scoring, dataset,
                             sparsity_threshold, acni_percentile=95.0, hpai_percentile=95.0,
                             force=False, plot=True, out_dir=None,
                             memory=Memory(cachedir='nilearn_cache')):
    """
    For a given n_components, load summary csvs if they already exist, or
    run main.py to get and save necessary summary data required for plotting.

    Returns (wb_summary, R_summary, L_summary), each of which are DataFrame.
    """
    # Directory to find or save the summary csvs
    out_dir = out_dir or op.join('ica_imgs', dataset, 'analyses', str(n_components))
    summary_csvs = ["wb_summary.csv", "R_summary.csv", "L_summary.csv"]

    # If summary data are already saved as csv files, simply load them
    if not force and all([op.exists(op.join(out_dir, csv)) for csv in summary_csvs]):
        print("Loading summary data from %s" % out_dir)
        (wb_summary, R_summary, L_summary) = (pd.read_csv(op.join(out_dir, csv))
                                              for csv in summary_csvs)

    # Otherwise run match analysis and save them as csv files
    else:
        # Initialize summary DFs
        (wb_summary, R_summary, L_summary) = (pd.DataFrame(
            {"n_comp": [n_components] * n_components}) for i in range(3))
        if not op.exists(out_dir):
            os.makedirs(out_dir)

        # Use wb matching in match analysis to get component images and
        # matching scores
        match_method = 'wb'
        img_d, score_mats_d, sign_mats_d = do_match_analysis(
            dataset=dataset, images=images, term_scores=term_scores,
            key=match_method, force=False, plot=plot,
            plot_dir=out_dir, n_components=n_components, scoring=scoring)

        # 1) For each of "wb", "R", and "L" image, get sparsity and ACNI
        # (Anti-Correlated Network index). For "wb", also get HPAI
        # (Hemispheric participation asymmetry index).
        hemis = ("R", "L", "wb")
        sparsityTypes = ("l1", "vc-pos", "vc-neg", "vc-abs")

        # Dict of DF and labels used to get and store results
        label_dict = {"wb": (wb_summary, hemis),
                      "R": (R_summary, ["R"]),
                      "L": (L_summary, ["L"])}

        for key in label_dict:
            (df, labels) = label_dict[key]

            # 1-1) Sparsity
            # sparsity_results = {label: sparsity_dict}
            sparsity_results = {label: get_hemi_sparsity(img_d[key], label,
                                thr=sparsity_threshold) for label in labels}

            for s_type in sparsityTypes:
                for label in labels:
                    df["%s_%s" % (s_type, label)] = sparsity_results[label][s_type]

            # 1-2) ACNI
            for label in labels:
                df["ACNI_%s" % label] = calculate_acni(
                    img_d[key], hemi=label, percentile=acni_percentile)

            # 1-3) For wb only, also compute HPAI
            if key == "wb":
                hpai_d = calculate_hpai(img_d[key], percentile=hpai_percentile)
                for sign in SPARSITY_SIGNS:
                    df["%sHPAI" % sign] = hpai_d[sign]

        # Save R/L_summary DFs
        R_summary.to_csv(op.join(out_dir, "R_summary.csv"))
        L_summary.to_csv(op.join(out_dir, "L_summary.csv"))

        # 2) Get SSS of wb component images as well as matched RL images
        col_img_pairs = [("wb_SSS", img_d["wb"]),
                         ("matchedRL_SSS", img_d["RL-unforced"])]
        for (col, img) in col_img_pairs:
            score_arr = compare_RL(img)
            wb_summary[col] = score_arr

        # 3) Finally store indices of matched R, L, and RL components, and the
        # respective match scores against wb
        comparisons = [('wb', 'R'), ('wb', 'L'), ('wb', 'RL-unforced')]
        for comparison in comparisons:
            score_mat, sign_mat = score_mats_d[comparison], sign_mats_d[comparison]
            matched, unmatched = get_match_idx_pair(score_mat, sign_mat)
            # Component indices for matched R, L , RL are in matched["idx"][1].
            # Multiply it by matched["sign"][1], which stores sign flipping info.
            matched_indices = matched["idx"][1] * matched["sign"][1]
            wb_summary["matched%s" % comparison[1]] = matched_indices

            matched_scores = score_mat[matched["idx"][0], matched["idx"][1]]
            wb_summary["match%s_score" % comparison[1]] = matched_scores
            num_unmatched = unmatched["idx"].shape[1] if unmatched["idx"] is not None else 0
            wb_summary["n_unmatched%s" % comparison[1]] = num_unmatched

            # Save wb_summary
            wb_summary.to_csv(op.join(out_dir, "wb_summary.csv"))

    return (wb_summary, R_summary, L_summary)


def generate_component_specific_plots(wb_master, components, scoring, out_dir=None):
    """Asdf"""
    start_idx = 0
    for c in components:
        wb_summary = wb_master[start_idx:(start_idx + c)]
        assert len(wb_summary) == c
        start_idx += c

        ### Generate component-specific plots ###
        # Save component-specific images in the component dir
        comp_outdir = op.join(out_dir, str(c))

        # 1) Relationship between positive and negative HPAI in wb components
        out_path = op.join(comp_outdir, "1_PosNegHPAI_%dcomponents.png" % c)

        # set color to the ACNI: ranging from 0 to 1 and reflects the proportion
        # of anti-correlated network (higher vals indicate strong ACN)
        color = wb_summary["ACNI_wb"]

        # size is proportional to vc-abs_wb
        size = wb_summary["rescaled_vc_abs"]
        ax = wb_summary.plot.scatter(x='posHPAI', y='negHPAI', c=color, s=size,
                                     xlim=(-1.1, 1.1), ylim=(-1.1, 1.1), edgecolors="grey",
                                     colormap='rainbow_r', colorbar=True, figsize=(7, 6))
        title = ax.set_title("\n".join(wrap("The relationship between HPAI on "
                                            "positive and negative side: "
                                            "n_components = %d" % c, 60)))
        ax.spines['right'].set_color('none')
        ax.spines['top'].set_color('none')
        ax.yaxis.set_ticks_position('left')
        ax.yaxis.set_label_coords(-0.1, 0.5)
        ax.spines['left'].set_position(('data', 0))
        ax.xaxis.set_ticks_position('bottom')
        ax.spines['bottom'].set_position(('data', 0))
        ticks = [-1.1, -1.0, -0.5, 0, 0.5, 1.0, 1.1]
        labels = ['L', '-1.0', '-0.5', '0', '0.5', '1.0', 'R']
        plt.setp(ax, xticks=ticks, xticklabels=labels, yticks=ticks, yticklabels=labels)
        f = plt.gcf()
        title.set_y(1.05)
        f.subplots_adjust(top=0.8)
        cax = f.get_axes()[1]
        cax.set_ylabel('Proportion of anti-correlated network',
                       rotation=270, labelpad=20)

        save_and_close(out_path)

        # 2) Relationship between HPAI and SSS in wb components
        out_path = op.join(comp_outdir, "2_HPAIvsSSS_%dcomponents.png" % c)

        fh, axes = plt.subplots(1, 3, sharey=True, figsize=(18, 6))
        fh.suptitle("The relationship between HPAI values and SSS: "
                    "n_components = %d" % c, fontsize=16)
        colors = sns.color_palette("Paired", 6)
        hpai_colors = {'pos': (colors[4], colors[5]),
                       'neg': (colors[0], colors[1]),
                       'abs': (colors[2], colors[3])}
        for ax, sign in zip(axes, SPARSITY_SIGNS):
            size = wb_summary['rescaled_vc_%s' % sign]
            ax.scatter(wb_summary['%sHPAI' % sign], wb_summary['wb_SSS'],
                       c=hpai_colors[sign][0], s=size,
                       edgecolors=hpai_colors[sign][1])
            ax.set_xlabel("%s HPAI" % sign)
            ax.set_xlim(-1.1, 1.1)
            ax.set_ylim(0, 1)
            ax.spines['right'].set_color('none')
            ax.spines['top'].set_color('none')
            ax.spines['left'].set_position(('data', 0))
            ax.xaxis.set_ticks_position('bottom')
            ax.spines['bottom'].set_position(('data', 0))
            plt.setp(ax, xticks=ticks, xticklabels=labels)
            fh.text(0.04, 0.5, "Spatial Symmetry Score using %s" % scoring,
                    va='center', rotation='vertical')

        save_and_close(out_path)


def _generate_plot_1(wb_master, sparsity_threshold, out_dir):
    # 1) HPAI-for pos, neg, and abs in wb components
    print "Plotting HPAI of wb components"
    out_path = op.join(out_dir, '1_wb_HPAI.png')

    fh, axes = plt.subplots(1, 3, sharex=True, sharey=True, figsize=(18, 6))
    fh.suptitle("Hemispheric Participation Asymmetry Index for each component", fontsize=16)
    colors = sns.color_palette("Paired", 6)
    hpai_styles = {'pos': (colors[4], colors[5], 'for correlated network'),
                   'neg': (colors[0], colors[1], 'for anti-correlated network'),
                   'abs': (colors[2], colors[3], 'overall')}
    by_comp = wb_master.groupby("n_comp")
    for ax, sign in zip(axes, SPARSITY_SIGNS):
        mean, sd = by_comp.mean()["%sHPAI" % sign], by_comp.std()["%sHPAI" % sign]
        ax.fill_between(components, mean + sd, mean - sd, linewidth=0,
                        facecolor=hpai_styles[sign][0], alpha=0.5)
        size = wb_master['rescaled_vc_%s' % (sign)]
        ax.scatter(wb_master.n_comp, wb_master["%sHPAI" % sign], label=sign,
                   c=hpai_styles[sign][1], s=size, edgecolors="grey")
        ax.plot(components, mean, c=hpai_styles[sign][1])
        ax.set_xlim((0, components[-1] + 5))
        ax.set_ylim((-1, 1))
        ax.set_xticks(components)
        ax.set_ylabel("HPAI((R-L)/(R+L) %s" % (hpai_styles[sign][2]))
    fh.text(0.5, 0.04, "Number of components", ha="center")

    save_and_close(out_path, fh=fh)


def _generate_plot_2_3(wb_master, R_master, L_master, out_dir):
    # 2) VC and 3) L1 Sparsity comparison between wb and hemi components
    print "Plotting sparsity for WB and hemi-components"
    pastel2 = sns.color_palette("Pastel2")
    set2 = sns.color_palette("Set2")
    hemi_colors = {"R": [set2[2], pastel2[2]], "L": [set2[0], pastel2[0]]}
    # Prepare summary of sparsity for each hemisphere
    for hemi, hemi_df in zip(("R", "L"), (R_master, L_master)):
        wb_sparsity = wb_master[hemi_df.columns]
        wb_sparsity["decomposition_type"] = "wb"
        hemi_df["decomposition_type"] = hemi
        sparsity_summary = wb_sparsity.append(hemi_df)

        # First plot voxel count sparsity
        out_path = op.join(out_dir, '2_vcSparsity_comparison_%s.png' % hemi)

        fh, axes = plt.subplots(1, 3, sharex=True, sharey=True, figsize=(18, 6))
        fh.suptitle("Voxel Count Sparsity of each component: Comparison of WB "
                    "and %s-only decomposition" % hemi, fontsize=16)
        colors = sns.color_palette("Paired", 6)
        sparsity_styles = {'pos': [colors[5], colors[4]],
                           'neg': [colors[1], colors[0]],
                           'abs': [colors[3], colors[2]]}
        for ax, sign in zip(axes, SPARSITY_SIGNS):
            sns.boxplot(x="n_comp", y="vc-%s_%s" % (sign, hemi), ax=ax,
                        hue="decomposition_type", data=sparsity_summary,
                        palette=sparsity_styles[sign])
            ax.set_title("%s" % sign)
        fh.text(0.04, 0.5, "Voxel Count Sparsity values", va='center',
                rotation='vertical')
        fh.text(0.5, 0.04, "Number of components", ha="center")

        save_and_close(out_path, fh=fh)

        # Next L1 norm sparsity
        out_path = op.join(out_dir, '3_l1Sparsity_comparison_%s.png' % hemi)

        fh = plt.figure(figsize=(10, 6))
        ax = fh.gca()
        plt.title("L1 Sparsity of each component: Comparison of WB "
                  "and %s-only decomposition" % hemi, fontsize=16)
        sns.boxplot(x="n_comp", y="l1_%s" % hemi, ax=ax,
                    hue="decomposition_type", data=sparsity_summary,
                    palette=hemi_colors[hemi])
        ax.set_xlabel("Number of components")
        ax.set_ylabel("L1 sparsity values")

        save_and_close(out_path, fh=fh)


def _generate_plot_4(wb_master, scoring, out_dir):

    # 4) Matching results: average matching scores and proportion of unmatched
    print "Plotting matching results"
    set2 = sns.color_palette("Set2")
    palette = [set2[2], set2[0], set2[1]]
    title = "Matching scores for the best-matched pairs"
    xlabel = "Number of components"
    ylabel = "Matching score using %s" % scoring

    out_path = op.join(out_dir, '4_Matching_results_box.png')
    score_cols = ["matchR_score", "matchL_score", "matchRL-unforced_score"]
    match_scores = pd.melt(wb_master[["n_comp"] + score_cols], id_vars="n_comp",
                           value_vars=score_cols)

    fh = plt.figure(figsize=(10, 6))
    plt.title(title)
    ax = sns.boxplot(x="n_comp", y="value", hue="variable", data=match_scores, palette=palette)
    ax.set(xlabel=xlabel, ylabel=ylabel)

    save_and_close(out_path, fh=fh)

    # Same data but in line plot: also add proportion of unmatched
    out_path = op.join(out_dir, '4_Matching_results_line.png')

    unmatch_cols = ["n_unmatchedR", "n_unmatchedL"]
    unmatched = pd.melt(wb_master[["n_comp"] + unmatch_cols], id_vars="n_comp",
                        value_vars=unmatch_cols)
    unmatched["proportion"] = unmatched.value / unmatched.n_comp.astype(float)

    fh = plt.figure(figsize=(10, 6))
    plt.title(title)
    ax = sns.pointplot(x="n_comp", y="value", hue="variable", palette=palette,
                       data=match_scores, dodge=0.3)
    sns.pointplot(x="n_comp", y="proportion", hue="variable", palette=palette,
                  data=unmatched, dodge=0.3, ax=ax, linestyles="--", markers="s")
    ax.set(xlabel=xlabel, ylabel=ylabel)
    fh.text(0.95, 0.5, "Proportion of unmatched R- or L- components", va="center", rotation=-90)

    save_and_close(out_path, fh=fh)


def _generate_plot_5(wb_master, scoring, out_dir):

    # 5) SSS for wb components and matched RL components
    print "Plotting SSS for wb components"
    pastel2 = sns.color_palette("Pastel2")
    set2 = sns.color_palette("Set2")
    palette = [set2[1], pastel2[1]]
    title = "Spatial Symmetry Score for WB and the matched RL components"
    xlabel = "Number of components"
    ylabel = "Spatial Symmetry Score using %s " % scoring

    out_path = op.join(out_dir, '5_wb_RL_SSS_box.png')

    sss_cols = ["wb_SSS", "matchedRL_SSS"]
    sss = pd.melt(wb_master[["n_comp"] + sss_cols], id_vars="n_comp",
                  value_vars=sss_cols)

    fh = plt.figure(figsize=(10, 6))
    plt.title(title)
    ax = sns.boxplot(x="n_comp", y="value", hue="variable", data=sss, palette=palette)
    ax.set(xlabel=xlabel, ylabel=ylabel)

    save_and_close(out_path, fh=fh)

    # Same data but with paired dots and lines
    out_path = op.join(out_dir, '5_wb_RL_SSS_dots.png')

    fh = plt.figure(figsize=(10, 6))
    plt.title(title)

    # first plot lines between individual plots
    for i in range(len(wb_master.index)):
        linestyle = "-" if (wb_master.wb_SSS[i] - wb_master.matchedRL_SSS[i]) < 0 else "--"
        plt.plot([wb_master.n_comp.astype(int)[i] - 1, wb_master.n_comp.astype(int)[i] + 1],
                 [wb_master.wb_SSS[i], wb_master.matchedRL_SSS[i]],
                 c="grey", linestyle=linestyle, linewidth=1.0)

    # add scatter points
    plt.scatter(wb_master.n_comp.astype(int) - 1, wb_master.wb_SSS, s=80,
                edgecolor="orange", facecolor=set2[1], label="WB")
    plt.scatter(wb_master.n_comp.astype(int) + 1, wb_master.matchedRL_SSS, s=80,
                edgecolor="orange", facecolor=pastel2[1], label="matched RL")
    plt.legend()

    # add mean change
    by_comp = wb_master.groupby("n_comp")
    for c, grouped in by_comp:
        linestyle = "-" if (grouped.wb_SSS.mean() - grouped.matchedRL_SSS.mean()) < 0 else "--"
        plt.plot([int(c) - 1, int(c) + 1], [grouped.wb_SSS.mean(), grouped.matchedRL_SSS.mean()],
                 c="black", linestyle=linestyle)
    comp_arr = np.asarray(map(int, components))
    plt.scatter(comp_arr - 1, by_comp.wb_SSS.mean(), c="orange", s=100, marker="+")
    plt.scatter(comp_arr + 1, by_comp.matchedRL_SSS.mean(), c="orange", s=100, marker="+")
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)

    save_and_close(out_path, fh=fh)


def _generate_plot_6(wb_master, R_master, L_master, out_dir):
    # 6) Plot ACNI for wb and hemi-components
    print "Generating plots of ACNI for wb and hemi-components"
    set2 = sns.color_palette("Set2")
    palette = [set2[2], set2[0], set2[1]]
    # Prepare ACNI for wb and hemi-components
    acni_cols = ["n_comp", "ACNI", "decomposition_type"]
    acni_summary = pd.DataFrame(columns=acni_cols)
    hemis = ("wb", "R", "L")
    master_DFs = (wb_master, R_master, L_master)
    for hemi, df in zip(hemis, master_DFs):
        acni = df[["n_comp", "ACNI_%s" % hemi]]
        acni["decomposition_type"] = hemi
        acni.columns = acni_cols
        acni_summary = acni_summary.append(acni)
    acni_summary["n_comp"] = acni_summary.n_comp.astype(int)
    out_path = op.join(out_dir, "6_ACNI_comparison.png")

    fh = plt.figure(figsize=(10, 6))
    ax = fh.gca()
    title = "\n".join(wrap("Anti-Correlated Network Index of each component: "
                           "Comparison of WB and R- or L-only decomposition", 60))
    plt.title(title, fontsize=16)
    sns.boxplot(x="n_comp", y="ACNI", ax=ax, hue="decomposition_type",
                data=acni_summary, palette=palette)
    ax.set_xlabel("Number of components")
    ax.set_ylabel("Proportion of Anti-Correlated Network")

    save_and_close(out_path, fh=fh)


def loop_main_and_plot(components, scoring, dataset, query_server=True,
                       force=False, plot=True, max_images=np.inf,
                       memory=Memory(cachedir='nilearn_cache')):
    """
    Loop main.py to plot summaries of WB vs hemi ICA components
    """
    out_dir = op.join('ica_imgs', dataset, 'analyses')

    # Get data once
    images, term_scores = get_dataset(dataset, max_images=max_images,
                                      query_server=query_server)

    # Perform ICA for WB, R and L for each n_component once and get images
    hemis = ("wb", "R", "L")
    imgs = {hemi: [] for hemi in hemis}
    for hemi in ("wb", "R", "L"):
        for c in components:
            print("Generating or loading ICA components for %s,"
                  " n=%d components" % (hemi, c))
            nii_dir = op.join('ica_nii', dataset, str(c))
            kwargs = dict(images=[im['local_path'] for im in images],
                          n_components=c, term_scores=term_scores,
                          out_dir=nii_dir, memory=memory)

            img = load_or_generate_components(
                hemi=hemi, force=force, no_plot=not plot, **kwargs)
            imgs[hemi].append(img)

    # Use wb images to determine threshold for voxel count sparsity
    print("Getting sparsity threshold.")
    global_percentile = 99.9
    sparsity_threshold = get_sparsity_threshold(
        images=imgs["wb"], global_percentile=global_percentile)
    print("Using global sparsity threshold of %0.8f for sparsity calculation"
          % sparsity_threshold)

    # Loop again this time to get values of interest and generate summary.
    # Note that if force, summary are calculated again but ICA won't be repeated.
    (wb_master, R_master, L_master) = (pd.DataFrame() for i in range(3))
    for c in components:
        print("Running analysis with %d components" % c)
        (wb_summary, R_summary, L_summary) = load_or_generate_summary(
            images=images, term_scores=term_scores, n_components=c,
            scoring=scoring, dataset=dataset, sparsity_threshold=sparsity_threshold,
            acni_percentile=95.0, hpai_percentile=95.0, force=force, memory=memory)
        # Append them to master DFs
        wb_master = wb_master.append(wb_summary)
        R_master = R_master.append(R_summary)
        L_master = L_master.append(L_summary)

    # Reset indices of master DFs and save
    master_DFs = dict(
        wb_master=wb_master, R_master=R_master, L_master=L_master)
    print "Saving summary csvs..."
    for key in master_DFs:
        master_DFs[key].reset_index(inplace=True)
        master_DFs[key].to_csv(op.join(out_dir, '%s_summary.csv' % key))

    # Generate plots
    # To set size proportional to vc sparsity in several graphs, add columns with
    # vc vals
    for sign in SPARSITY_SIGNS:
        wb_master["rescaled_vc_%s" % sign] = rescale(wb_master["vc-%s_wb" % sign])

    # 1) Component-specific plots
    print "Generating plots for each n_components."
    generate_component_specific_plots(
        wb_master=wb_master, components=components, scoring=scoring, out_dir=out_dir)

    # 2) Main summary plots over the range of n_components
    print "Generating summary plots.."
    _generate_plot_1(wb_master=wb_master, sparsity_threshold=sparsity_threshold,
                     out_dir=out_dir)
    _generate_plot_2_3(out_dir=out_dir, **master_DFs)
    _generate_plot_4(wb_master=wb_master, scoring=scoring, out_dir=out_dir)
    _generate_plot_5(wb_master=wb_master, scoring=scoring, out_dir=out_dir)
    _generate_plot_6(out_dir=out_dir, **master_DFs)


if __name__ == '__main__':
    import warnings
    from argparse import ArgumentParser

    # Look for image computation errors
    warnings.simplefilter('ignore', DeprecationWarning)
    warnings.simplefilter('error', RuntimeWarning)  # Detect bad NV images

    # Arg parsing
    hemi_choices = ['R', 'L', 'wb']
    parser = ArgumentParser(description="Really?")
    parser.add_argument('--force', action='store_true', default=False)
    parser.add_argument('--offline', action='store_true', default=False)
    parser.add_argument('--no-plot', action='store_true', default=False)
    parser.add_argument('--components', nargs='?',
                        default="5,10,15,20,30,40,50,75,100")
    parser.add_argument('--dataset', nargs='?', default='neurovault',
                        choices=['neurovault', 'abide', 'nyu'])
    parser.add_argument('--scoring', nargs='?', default='correlation',
                        choices=['l1norm', 'l2norm', 'correlation'])
    parser.add_argument('--max-images', nargs='?', type=int, default=np.inf)
    args = vars(parser.parse_args())

    # Alias args
    query_server = not args.pop('offline')
    plot = not args.pop('no_plot')
    components = [int(c) for c in args.pop('components').split(',')]

    loop_main_and_plot(
        components=components, query_server=query_server, plot=plot, **args)
