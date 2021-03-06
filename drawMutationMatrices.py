#!/usr/bin/python
import sys
import os
if 'HOTNET' not in os.environ:
    raise Error('Could not find HotNet2 installation. Please add HOTNET environment variable.')
sys.path.insert(0, os.environ['HOTNET'])

import argparse
import json
import hnio
from collections import defaultdict, namedtuple
from constants import *

def parse_args(raw_args):
    description = 'Draws mutation matrices for the given subnetworks and mutation data.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-s', '--subnetworks_file', required=True,
                        help='Path to tab-separated file listing components for which mutation matrices\
                              should be generated, one component per line.')
    parser.add_argument('-v', '--snv_file',
                        help='Path to a tab-separated file containing SNVs where the first column\
                              of each line is a sample ID and subsequent columns contain the names\
                              of genes with SNVs in that sample. Lines starting with "#" will be\
                              ignored.')
    parser.add_argument('-c', '--cna_file',
                        help='Path to a tab-separated file containing CNAs where the first column\
                              of each line is a sample ID and subsequent columns contain gene\
                              names followed by "(A)" or "(D)" indicating an amplification or\
                              deletion in that gene for the sample. Lines starting with "#" will\
                              be ignored.')
    parser.add_argument('-i', '--inactivating_snvs_file',
                        help='Path to a tab-separated file listing inactivating mutations where\
                              the first column of each line is a gene name and the second column\
                              is a sample ID. Lines starting with "#" will be ignored.')
    parser.add_argument('-f', '--fusions_file',
                        help='Path to a tab-separated file listing fusions where the first column\
                              of each line is a sample ID and the next two columns are gene names.\
                              Lines starting with "#" will be ignored.')
    parser.add_argument('-p', '--sample_file',
                        help='File listing samples. Any SNVs or CNAs in samples not listed in this\
                              file will be ignored. If not provided, the set of samples is assumed\
                              to be all samples that are provided in the SNV or CNA data.')
    parser.add_argument('-g', '--gene_file',
                        help='File listing tested genes. Any SNVs or CNAs in genes not listed in\
                              this file will be ignored. If not provided, the set of tested genes\
                              is assumed to be all genes that are provided in the SNV or CNA data.')
    parser.add_argument('-t', '--type_file',
                        help='Path to tab-separated file listing sample types where the first\
                              column of each line is a sample ID and the second column is a type.\
                              If not provided, all samples are assumed to be of the same default\
                              type.')
    parser.add_argument('-y', '--style_file', default='js/styles/default-style.json',
                        help='Path JSON file defining styles. If not provided, sensible defaults\
                              will be used.')
    parser.add_argument('-l', '--color_file',
                        help='Path to tab-separated file listing a sample type in the first column\
                              and the color that should be used for that sample type in the second\
                              column. Note that this color map overwrites any that might be\
                              included in the style file. If none provided and there is a single\
                              sample type, samples will be colored according to exclusivity and\
                              co-occurrence. Otherwise, samples will be colored by type with an\
                              arbitrary color mapping.')
    parser.add_argument('-w', '--width', type=int, default=900,
                        help='Width in pixels for mutation matrices.')
    parser.add_argument('-o', '--output_directory', required=True,
                        help='Output directory in which mutation matrices should be generated.')
    args = parser.parse_args(raw_args)

    #validate arguments
    if not args.snv_file and not args.cna_file:
        raise ValueError('At least one of snv_file and cna_file must be provided.')

    return args

def valid_cna_filter_thresh(string):
        value = float(string)
        if value <= .5:
            raise argparse.ArgumentTypeError("cna_filter_threshold must be > .5")
        return value

def write_style_file(input_style_file, color_file, output_style_file):
    styles = json.load(open(input_style_file))

    if color_file:
        arrs = [line.split() for line in open(color_file)]
        sampleColors = dict([(arr[0], arr[1]) for arr in arrs])
        styles['global']['colorSchemes']['sampleType'] = sampleColors

    json.dump(styles, open(output_style_file, 'w'), indent=4)

DEFAULT_TYPE = 'DEFAULT'

def get_data_for_cc(cc, snvs, cnas, inactivating_snvs, fusions, sample2type):
    cc = set(cc)
    data = dict()
    samples = set()
    
    # INACTIVE_SNVs replace SNVs, but all other combinations can coexist
    M = defaultdict(lambda: defaultdict(list))
    for mut in inactivating_snvs:
        if mut.gene in cc:
            M[mut.gene][mut.sample] = [mut.mut_type]
            samples.add(mut.sample)
    
    for mut in snvs + cnas:
        if mut.gene in cc and (mut.mut_type != SNV or INACTIVE_SNV not in M[mut.gene][mut.sample]):
            M[mut.gene][mut.sample].append(mut.mut_type)
            samples.add(mut.sample)
    data['M'] = M

    for fusion in fusions:
        if fusion.genes[0] in cc:
            M[fusion.genes[0]][fusion.sample].append(FUSION)
            samples.add(fusion.sample)
        if fusion.genes[1] in cc:
            M[fusion.genes[1]][fusion.sample].append(FUSION)
            samples.add(fusion.sample)

    if sample2type:
        reduced_s2t = dict()
        for sample in samples:
            if sample not in sample2type:
                print "WARNING: sample %s is not in type file; assigning type %s" % (sample, DEFAULT_TYPE)
                reduced_s2t[sample] = DEFAULT_TYPE
            else:
                reduced_s2t[sample] = sample2type[sample]
    else:
        reduced_s2t = dict((s, DEFAULT_TYPE) for s in samples)

    # Count the number of samples of each type
    typeToNumSamples = dict( (ty, 0) for ty in set(sample2type.values()) )
    for s, ty in sample2type.iteritems(): typeToNumSamples[ty] += 1

    data['sampleToTypes'] = reduced_s2t
    data['typeToNumSamples'] = typeToNumSamples
    data['sampleTypes'] = list(set(sample2type.values())) if sample2type else [DEFAULT]
    
    return data

Subnetwork = namedtuple('Subnetwork', ['index', 'genes'])

def get_subnetworks(subnetwork_file):
    arrs = [line.split() for line in open(subnetwork_file)]
    return [Subnetwork(i, arrs[i]) for i in range(len(arrs))]

def run(args):
    # create output directory if doesn't exist; warn if it exists and is not empty
    if not os.path.exists(args.output_directory):
        os.makedirs(args.output_directory)
    if len(os.listdir(args.output_directory)) > 0:
        print("WARNING: Output directory is not empty. Any conflicting files will be overwritten. "
              "(Ctrl-c to cancel).")

    samples = hnio.load_samples(args.sample_file) if args.sample_file else None
    genes = hnio.load_genes(args.gene_file) if args.gene_file else None
    snvs = hnio.load_snvs(args.snv_file, genes, samples) if args.snv_file else []
    cnas = hnio.load_cnas(args.cna_file, genes, samples) if args.cna_file else []
    inactivating_snvs = hnio.load_inactivating_snvs(args.inactivating_snvs_file, genes, samples) \
                            if args.inactivating_snvs_file else []
    fusions = hnio.load_fusions(args.fusions_file, genes, samples) if args.fusions_file else []
    sample2type = hnio.load_sample_types(args.type_file) if args.type_file else None
    subnetworks = get_subnetworks(args.subnetworks_file)

    write_style_file(args.style_file, args.color_file, '%s/styles.json' % args.output_directory)
    
    for subnetwork in subnetworks:
        print "Generating mutation matrix for subnetwork %s" % subnetwork.index
        data = get_data_for_cc(subnetwork.genes, snvs, cnas, inactivating_snvs, fusions, sample2type)
        json.dump(data, open('%s/data.json' % args.output_directory, 'w'), indent=4)

        os.system('node drawMutationMatrix.js --json=%s/data.json --outdir=%s --width=%s --style=%s' % 
            (args.output_directory, args.output_directory, args.width, '%s/styles.json' % args.output_directory))
        os.rename('%s/mutation_matrix.svg' % args.output_directory,
         		  '%s/mutation_matrix_%s.svg' % (args.output_directory, subnetwork.index))

    os.remove('%s/data.json' % args.output_directory)
    os.remove('%s/styles.json' % args.output_directory)

if __name__ == "__main__":
    run(parse_args(sys.argv[1:]))
