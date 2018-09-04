"""
The MIT License (MIT)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import requests
from requests.exceptions import HTTPError
import pandas as pd
import numpy as np
import scipy
import json
import logging
from logging.config import dictConfig

ENCODE_BASE_URL = 'https://www.encodeproject.org'
MAD_SEARCH_URL = ('/search/?type=MadQualityMetric'
                  '&status=released&assay_term_name=RNA-seq'
                  '&assay_term_name=shRNA+knockdown+followed+by+RNA-seq'
                  '&assay_term_name=CRISPRi+followed+by+RNA-seq'
                  '&assay_term_name=CRISPR+genome+editing+followed+by+RNA-seq'
                  '&assay_term_name=siRNA+knockdown+followed+by+RNA-seq'
                  '&assay_term_name=single+cell+isolation+followed+by+RNA-seq'
                  '&frame=embedded'
                  '&format=json'
                  '&limit=all')
EXPERIMENT_SEARCH_URL = (
    '/search/?status=released'
    '&type=Experiment'
    '&assay_term_name=RNA-seq'
    '&assay_term_name=shRNA+knockdown+followed+by+RNA-seq'
    '&assay_term_name=CRISPRi+followed+by+RNA-seq'
    '&assay_term_name=CRISPR+genome+editing+followed+by+RNA-seq'
    '&assay_term_name=siRNA+knockdown+followed+by+RNA-seq'
    '&assay_term_name=single+cell+isolation+followed+by+RNA-seq'
    '&frame=embedded'
    '&format=json'
    '&limit=all')


def get_metadata_object(query_url,
                        error_message,
                        logger,
                        raise_exception=False):
    """Gets data from query.
    Makes a GET request to queryurl.

    Args:
        query_url: url that the GET is sent to. json response
            expected
        error_message: if HTTPError occurs in request, message logged.
        logger: logger to log into
        raise_exception: boolean. Raise HTTPError after logging.
            defaults to False.
    Returns:
        A dict with the response content in it.
    Raises:
        HTTPError
    """
    r = requests.get(query_url)
    try:
        r.raise_for_status()
    except HTTPError as e:
        logger.exception(error_message)
        if raise_exception:
            raise (e)
    else:
        return r.json()


def build_file_to_experiment_data_mapping(experiment_data_dict):
    """Build a file-to-experiment lookup mapping.

    Args:
        experiment_data_dict: dict that contains experiment metadata.
        logger: where to send the logs

    Returns:
        Lookup mapping with keys of format '/files/<file_accession>',
        and a dict
        {
        'experiment_accession' : <experiment accession>,
        'replication_type' : <replication type>,
        'biosample_type' : <biosample type>,
        'assembly' : <assembly>
        }
        as values. If the file does not have the attribute assembly,
        'Not applicable' is output.
    """
    file_to_experiment_mapping = dict()
    for experiment in experiment_data_dict['@graph']:
        experiment_accession = experiment['accession']
        replication_type = experiment['replication_type']
        biosample_type = experiment['biosample_type']
        for file in experiment['files']:
            assembly = file.get('assembly', 'Not applicable')
            file_to_experiment_mapping[file['@id']] = {
                'experiment_accession': experiment_accession,
                'replication_type': replication_type,
                'biosample_type': biosample_type,
                'assembly': assembly
            }
    return file_to_experiment_mapping


def get_min_quantile_value(dataframe1, dataframe2, column, quant):
    """Calculate minimum of quantile thresholds.
    Given two pandas DataFrames, and a column that is present in both of them,
    and a quantile, calculate threshold for both dataframes, and return the
    smaller one.

    Args:
        dataframe1: Pandas DataFrame
        dataframe2: Pandas DataFrame
        column: String column name that is present in both input DataFrames
        quant: float percentile. For top 1% enter 0.99 etc.

    Returns:
        threshold: float

    Raises:
        KeyError if column is not present in either of the inputs.
    """
    return min(
        dataframe1[column].quantile(q=quant),
        dataframe2[column].quantile(q=quant))


def build_record_of_correlation_metrics_from_madqc_obj(
        madqc_obj, file_to_experiment_mapping):
    """Take one madqc dict, and calculate correlation metric dict.

    Args:
        madqc_obj: dict containing a madQC object
        file_to_experiment_mapping: dict built by
            build_file_to_experiment_data_mapping.

    Returns:
        Dict with following structure:
            {
            'quality_metric_of': [file1, file2],
            'current_pearson' : pearson correlation from madQC,
            'current_spearman' : spearman correlation from madQC,
            'FPKM_gt_1_pearson' : pearson correlation between
                file1 and file2 FPKMs where entries for both files are greater
                than one. Log2 transform is applied before calculation,
            'FPKM_gt_1_spearman' : as above, but spearman,
            'FPKM_log2_mean_gt_0_pearson' : Log2 transformed
                pearson correlation. Mean of Log2(FPKM) less or equal than zero
                values are omitted,
            'FPKM_log2_mean_gt_0_spearman' : as above but spearman,
            'FPKM_log2_mean_gt_0_pearson_01_pct' : As above, but additionally
                top 0.1 percentile of FPKMs are omitted (threshold is
                calculated for both files and smaller is applied to both),
            'FPKM_log2_mean_gt_0_spearman_01_pct' : As above,
            'FPKM_log2_mean_gt_0_pearson_1_pct' : As above, but top 1
                percentile removed,
            'FPKM_log2_mean_gt_0_spearman_1_pct' : As above, but spearman,
            'FPKM_log2_mean_gt_0_pearson_10_pct' : As above, but top 10
                percentile removed,
            'FPKM_log2_mean_gt_0_spearman_1_pct' : As above, but spearman
            'assembly' : Assembly used in creating quants, in case these are
                not equal for both files, raise AssertionError,
            'experiment_accession' : accession of the experiment files are
                from. In case of mismatch between files, raise
                AssertionError.
            'replication_type' : isogenic or anisogenic,
            'biosample_type' : Biosample type of experiment
            }
    Raises:
        AssertionError if assemblies of files are not equal or files are not
        from same experiment.
    """
    # first check things that result in error, to fail as fast as possible.
    file1_meta = file_to_experiment_mapping[madqc_obj['quality_metric_of'][0]]
    file2_meta = file_to_experiment_mapping[madqc_obj['quality_metric_of'][1]]
    assert file1_meta['experiment_accession'] == file2_meta[
        'experiment_accession'], 'Mismatch in experiment accession.'
    assert file1_meta['assembly'] == file2_meta[
        'assembly'], 'Mismatch in assembly.'
    correlation_record = dict()
    correlation_record['quality_metric_of'] = madqc_obj['quality_metric_of']
    correlation_record['current_pearson'] = madqc_obj['Pearson correlation']
    correlation_record['current_spearman'] = madqc_obj['Spearman correlation']
    correlation_record['assembly'] = file1_meta['assembly']
    correlation_record['experiment_accession'] = file1_meta[
        'experiment_accession']
    correlation_record['replication_type'] = file1_meta['replication_type']
    correlation_record['biosample_type'] = file1_meta['biosample_type']
    return correlation_record


if __name__ == '__main__':
    with open('logger_config.json') as f:
        logger_config = json.load(f)

    dictConfig(logger_config)
    logger = logging.getLogger(__name__)

    logger.info('Getting madQC metadata objects.')
    mad_request = requests.get(ENCODE_BASE_URL + MAD_SEARCH_URL)
    mad_request.raise_for_status()
    mad_data = mad_request.json()
    logger.info('Getting experiment metadata objects.')
    experiment_request = requests.get(ENCODE_BASE_URL + EXPERIMENT_SEARCH_URL)
    experiment_request.raise_for_status()
    experiment_data = experiment_request.json()
    logger.info('Building file -> experiment lookup mapping.')
    file_to_experiment_lookup = build_file_to_experiment_data_mapping(
        experiment_data, logger)
    logger.info(
        'Built mapping for {} files'.format(len(file_to_experiment_lookup)))
